"""Response-contract checks using an injected SDK boundary; no network requests."""

import json
from types import SimpleNamespace

import pytest

from noir.domain.config import NoirConfig
from noir.infrastructure.ai.gemini import GeminiProvider, GeminiProviderError


@pytest.fixture
def provider():
    return GeminiProvider(
        config=NoirConfig(_env_file=None, gemini_api_key="unit-test-only", ai_provider="gemini")
    )


def inject_response(monkeypatch, provider, text):
    client = SimpleNamespace(
        models=SimpleNamespace(generate_content=lambda **kwargs: SimpleNamespace(text=text))
    )
    monkeypatch.setattr(provider, "_get_client", lambda: client)


@pytest.mark.parametrize("text", [None, "", "   ", 42, {"status": "ok"}])
def test_non_text_or_empty_response_rejected(monkeypatch, provider, text):
    inject_response(monkeypatch, provider, text)
    with pytest.raises(GeminiProviderError, match="Empty or non-text"):
        provider._call_model("test")


def test_valid_response_preserved(monkeypatch, provider):
    text = '{"status":"ok"}'
    inject_response(monkeypatch, provider, text)
    assert provider._call_model("test") == text


def test_oversized_response_rejected(monkeypatch, provider):
    provider.config.ai_max_output_size = 4
    inject_response(monkeypatch, provider, "too long")
    with pytest.raises(GeminiProviderError, match="oversized"):
        provider._call_model("test")


@pytest.mark.parametrize("include_null_metadata", [True, False])
def test_text_patch_accepts_optional_null_or_missing_metadata(
    monkeypatch, provider, tmp_path, include_null_metadata
):
    from noir.domain.enums import PatchOperationType
    from noir.domain.models import ChangePlan, PlanFileChange
    from noir.infrastructure.filesystem.workspace import ProjectWorkspace, compute_file_hash
    from noir.patches.engine import PatchEngine

    config = provider.config.model_copy(update={"data_dir": str(tmp_path)})
    ws = ProjectWorkspace("nullable_metadata", config)
    ws.create()
    target = ws.decoded_dir / "AndroidManifest.xml"
    target.write_text('<manifest><application label="Original" /></manifest>')
    original = target.read_text()
    digest = compute_file_hash(target)
    plan = ChangePlan(
        project_id=ws.project_id,
        workspace_revision=0,
        user_request="Rename app",
        file_changes=[
            PlanFileChange(
                relative_path="AndroidManifest.xml", operation=PatchOperationType.REPLACE_BLOCK
            )
        ],
    )
    operation = {
        "relative_path": "AndroidManifest.xml",
        "operation": "replace_block",
        "match_content": 'label="Original"',
        "new_content": 'label="a1b2c3"',
        "expected_preimage_hash": "ignore-model-supplied-hash",
    }
    if include_null_metadata:
        operation.update(xml_attributes=None, affected_scope=None)
    inject_response(monkeypatch, provider, json.dumps({"operations": [operation]}))
    patch = provider.generate_patch(
        plan,
        {
            "file_snippets": {"AndroidManifest.xml": original},
            "file_hashes": {"AndroidManifest.xml": digest},
        },
    )
    assert patch.operations[0].xml_attributes == {}
    assert patch.operations[0].affected_scope == ""
    assert patch.operations[0].expected_preimage_hash == digest
    assert target.read_text() == original  # Generating a patch must not apply it.
    engine = PatchEngine(ws)
    assert "a1b2c3" in engine.generate_diff(patch)[0]["preview"]
    engine.apply_patch(patch)
    assert target.read_text() == '<manifest><application label="a1b2c3" /></manifest>'


def test_non_dictionary_xml_attributes_are_not_silently_discarded(provider):
    with pytest.raises(GeminiProviderError, match="invalid fields: xml_attributes"):
        provider._parse_patch_operation(
            {
                "relative_path": "test.xml",
                "operation": "replace_block",
                "match_content": "old",
                "new_content": "new",
                "xml_attributes": [],
            },
            0,
        )


def test_manifest_update_still_requires_attributes(provider):
    with pytest.raises(GeminiProviderError, match="nonempty xml_attributes"):
        provider._parse_patch_operation(
            {
                "relative_path": "AndroidManifest.xml",
                "operation": "manifest_update",
                "xml_element": "application",
                "xml_attributes": None,
            },
            0,
        )


def test_null_replacement_is_not_coerced_to_deletion(provider):
    with pytest.raises(GeminiProviderError, match="requires match_content and new_content"):
        provider._parse_patch_operation(
            {
                "relative_path": "test.xml",
                "operation": "replace_block",
                "match_content": "old",
                "new_content": None,
                "xml_attributes": None,
            },
            0,
        )


def test_malformed_patch_entry_returns_provider_error(provider):
    with pytest.raises(GeminiProviderError, match="operation 1 must be an object"):
        provider._parse_patch_operation(None, 0)
