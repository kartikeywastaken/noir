"""Response-contract checks using an injected SDK boundary; no network requests."""

import json
from types import SimpleNamespace

import pytest

from noir.domain.config import NoirConfig
from noir.infrastructure.ai.gemini import GeminiProvider, GeminiProviderError


@pytest.fixture
def provider():
    return GeminiProvider(
        config=NoirConfig(
            _env_file=None,
            gemini_api_key="unit-test-only",
            ai_provider="gemini",
            ai_model="gemini-3.7-flash",
            ai_fallback_model="gemini-3.6-flash",
        )
    )


def inject_response(monkeypatch, provider, text):
    client = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda **kwargs: SimpleNamespace(
                text=text, prompt_feedback=None, candidates=[SimpleNamespace(finish_reason="STOP")]
            )
        )
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


def test_gemini_37_uses_supported_generation_parameters(monkeypatch, provider):
    captured = {}

    def generate_content(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            text='{"status":"ok"}',
            prompt_feedback=None,
            candidates=[SimpleNamespace(finish_reason="STOP")],
        )

    monkeypatch.setattr(
        provider,
        "_get_client",
        lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)),
    )

    provider._call_model("test")

    assert provider.model_name == "gemini-3.7-flash"
    assert captured["config"].temperature is None


def test_gemini_37_capacity_failure_uses_gemini_36_fallback(monkeypatch, provider):
    calls = []

    class UnavailableError(Exception):
        status_code = 503

    def generate_content(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "gemini-3.7-flash":
            raise UnavailableError("503 UNAVAILABLE: high demand")
        return SimpleNamespace(
            text='{"status":"ok"}',
            prompt_feedback=None,
            candidates=[SimpleNamespace(finish_reason="STOP")],
        )

    monkeypatch.setattr(
        provider,
        "_get_client",
        lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)),
    )

    assert provider._call_model("test") == '{"status":"ok"}'
    assert calls == ["gemini-3.7-flash", "gemini-3.6-flash"]
    assert provider.last_model_name == "gemini-3.6-flash"


def test_configured_fallback_disables_slow_hidden_sdk_retries(monkeypatch, provider):
    from google import genai

    captured = {}

    def client(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(genai, "Client", client)
    provider._get_client()
    assert captured["http_options"].retry_options.attempts == 1


def test_invalid_request_does_not_use_fallback(monkeypatch, provider):
    calls = []

    class InvalidRequestError(Exception):
        status_code = 400

    def generate_content(**kwargs):
        calls.append(kwargs["model"])
        raise InvalidRequestError("400 INVALID_ARGUMENT")

    monkeypatch.setattr(
        provider,
        "_get_client",
        lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)),
    )

    with pytest.raises(GeminiProviderError, match="INVALID_ARGUMENT"):
        provider._call_model("test")
    assert calls == ["gemini-3.7-flash"]


def test_invalid_structured_request_retries_same_model_in_json_mode(monkeypatch, provider):
    calls = []

    class InvalidRequestError(Exception):
        status_code = 400

    def generate_content(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise InvalidRequestError("400 INVALID_ARGUMENT")
        return SimpleNamespace(
            text='{"status":"ok"}',
            prompt_feedback=None,
            candidates=[SimpleNamespace(finish_reason="STOP")],
        )

    monkeypatch.setattr(
        provider,
        "_get_client",
        lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)),
    )

    schema = {"type": "object", "required": ["status"]}
    assert provider._call_model("test", response_schema=schema) == '{"status":"ok"}'
    assert [call["model"] for call in calls] == ["gemini-3.7-flash"] * 2
    assert calls[0]["config"].response_json_schema == schema
    assert calls[1]["config"].response_json_schema is None
    assert '"required":["status"]' in calls[1]["contents"]


def test_gemini_36_uses_textual_schema_without_wasted_rejected_call(monkeypatch):
    provider = GeminiProvider(
        config=NoirConfig(
            _env_file=None,
            gemini_api_key="unit-test-only",
            ai_provider="gemini",
            ai_model="gemini-3.6-flash",
            ai_fallback_model="",
        )
    )
    calls = []

    def generate_content(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            text='{"status":"ok"}',
            prompt_feedback=None,
            candidates=[SimpleNamespace(finish_reason="STOP")],
        )

    monkeypatch.setattr(
        provider,
        "_get_client",
        lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)),
    )

    schema = {"type": "object", "required": ["status"]}
    assert provider._call_model("test", response_schema=schema) == '{"status":"ok"}'
    assert len(calls) == 1
    assert calls[0]["config"].response_json_schema is None
    assert '"required":["status"]' in calls[0]["contents"]


def test_cil_patch_schema_requires_complete_method_operation():
    from noir.domain.enums import PatchOperationType
    from noir.domain.models import ChangePlan, PlanFileChange
    from noir.infrastructure.ai.gemini import _patch_response_schema

    plan = ChangePlan(
        project_id="cil-schema",
        workspace_revision=0,
        user_request="change managed state",
        file_changes=[
            PlanFileChange(
                relative_path="assets/bin/Data/Managed/Assembly-CSharp.dll",
                operation=PatchOperationType.CIL_REPLACE_METHOD_BODY,
            )
        ],
    )

    item_schema = _patch_response_schema(plan)["properties"]["operations"]["items"]
    required = item_schema["required"]

    assert "assembly_name" in required
    assert "type_full_name" in required
    assert "method_signature" in required
    assert "new_il_source" in required
    assert "expected_method_il_hash" in required


def test_patch_schema_binds_each_operation_to_its_approved_path():
    from noir.domain.enums import PatchOperationType
    from noir.domain.models import ChangePlan, PlanFileChange
    from noir.infrastructure.ai.gemini import _patch_response_schema

    plan = ChangePlan(
        project_id="pair-binding",
        workspace_revision=0,
        user_request="change manifest label and web title",
        file_changes=[
            PlanFileChange(
                relative_path="AndroidManifest.xml",
                operation=PatchOperationType.MANIFEST_UPDATE,
            ),
            PlanFileChange(
                relative_path="assets/www/index.html",
                operation=PatchOperationType.REPLACE_BLOCK,
            ),
        ],
    )

    variants = _patch_response_schema(plan)["properties"]["operations"]["items"]["oneOf"]
    bindings = {
        (
            variant["properties"]["relative_path"]["enum"][0],
            variant["properties"]["operation"]["enum"][0],
        )
        for variant in variants
    }

    assert bindings == {
        ("AndroidManifest.xml", "manifest_update"),
        ("assets/www/index.html", "replace_block"),
    }
    assert all(
        not (
            variant["properties"]["relative_path"]["enum"] == ["AndroidManifest.xml"]
            and variant["properties"]["operation"]["enum"] == ["replace_block"]
        )
        for variant in variants
    )


def test_cil_parser_normalizes_exact_metadata_assembly_stem():
    operation = GeminiProvider._parse_patch_operation(
        {
            "relative_path": "assets/bin/Data/Managed/Assembly-CSharp.dll",
            "operation": "cil_replace_method_body",
            "assembly_name": "Assembly-CSharp",
            "type_full_name": "PlayerInfo",
            "method_signature": "System.Int32 get_amountOfCoins()",
            "new_il_source": "ldc.i4 100\nret",
            "expected_method_il_hash": "a" * 64,
        },
        0,
    )

    assert operation.assembly_name == "Assembly-CSharp.dll"


def test_cil_patch_binds_host_inspected_method_hash(monkeypatch, provider):
    from noir.domain.enums import PatchOperationType
    from noir.domain.models import ChangePlan, PlanFileChange

    relative = "assets/bin/Data/Managed/Assembly-CSharp.dll"
    signature = "System.Int32 get_amountOfCoins()"
    trusted_hash = "a" * 64
    plan = ChangePlan(
        project_id="cil-hash-binding",
        workspace_revision=0,
        user_request="change coins",
        file_changes=[
            PlanFileChange(
                relative_path=relative,
                operation=PatchOperationType.CIL_REPLACE_METHOD_BODY,
            )
        ],
    )
    inject_response(
        monkeypatch,
        provider,
        json.dumps(
            {
                "operations": [
                    {
                        "relative_path": relative,
                        "operation": "cil_replace_method_body",
                        "assembly_name": "Assembly-CSharp.dll",
                        "type_full_name": "PlayerInfo",
                        "method_signature": signature,
                        "new_il_source": "ldc.i4 999999999\nret",
                        "expected_method_il_hash": "b" * 64,
                    }
                ]
            }
        ),
    )

    patch = provider.generate_patch(
        plan,
        {
            "file_snippets": {},
            "file_hashes": {relative: "c" * 64},
            "binary_inspection": {
                relative: {
                    "selected_method_il": [
                        {
                            "type_full_name": "PlayerInfo",
                            "method_signature": signature,
                            "il_source": "ldarg.0\nldfld token:0x04000e2a\nret",
                            "il_hash": trusted_hash,
                        }
                    ]
                }
            },
        },
    )

    assert patch.operations[0].expected_method_il_hash == trusted_hash
    assert patch.operations[0].expected_preimage_hash == "c" * 64


def test_unsupported_plan_can_return_zero_file_changes(monkeypatch, provider):
    from noir.domain.models import AnalysisResult

    inject_response(
        monkeypatch,
        provider,
        json.dumps(
            {
                "intended_outcome": "The requested behavior cannot be changed safely",
                "file_changes": [],
                "unsupported_aspects": ["No evidence-backed implementation point was found"],
            }
        ),
    )

    plan = provider.generate_plan(
        "change internal behavior",
        AnalysisResult(project_id="unsupported"),
        {"files": [], "file_snippets": {}},
        project_id="unsupported",
    )

    assert plan.file_changes == []
    assert plan.unsupported_aspects


def test_plan_prompt_marks_confirmed_hermes_bundle_as_non_text(monkeypatch, provider):
    from noir.domain.models import AnalysisResult

    captured = {}

    def respond(prompt, system, *, response_schema):
        captured["prompt"] = prompt
        captured["system"] = system
        captured["schema"] = response_schema
        return json.dumps(
            {
                "intended_outcome": "The requested JavaScript behavior is unsupported",
                "file_changes": [],
                "unsupported_aspects": ["The app bundle is compiled Hermes bytecode"],
            }
        )

    monkeypatch.setattr(provider, "_call_model", respond)
    analysis = AnalysisResult(
        project_id="hermes-app",
        runtimes={"dalvik", "native", "react_native", "hermes"},
        runtime="hermes",
        runtime_evidence={
            "react_native": ["assets/index.android.bundle"],
            "hermes_bytecode": ["assets/index.android.bundle"],
        },
    )

    provider.generate_plan(
        "Change JavaScript behavior",
        analysis,
        {
            "files": ["AndroidManifest.xml", "assets/index.android.bundle"],
            "file_snippets": {"AndroidManifest.xml": "<manifest />"},
        },
        project_id=analysis.project_id,
    )

    assert '"hermes_bytecode"' in captured["prompt"]
    assert "assets/index.android.bundle" in captured["prompt"]
    assert "not JavaScript text" in captured["prompt"]


def test_empty_plan_without_unsupported_reason_is_rejected(monkeypatch, provider):
    from noir.domain.models import AnalysisResult

    inject_response(
        monkeypatch,
        provider,
        json.dumps(
            {
                "intended_outcome": "No changes",
                "file_changes": [],
                "unsupported_aspects": [],
            }
        ),
    )

    with pytest.raises(GeminiProviderError, match="neither actionable"):
        provider.generate_plan(
            "change internal behavior",
            AnalysisResult(project_id="invalid-empty"),
            {"files": [], "file_snippets": {}},
            project_id="invalid-empty",
        )


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


def test_manifest_add_requires_structured_attributes(provider):
    with pytest.raises(GeminiProviderError, match="nonempty xml_attributes"):
        provider._parse_patch_operation(
            {
                "relative_path": "AndroidManifest.xml",
                "operation": "manifest_add",
                "xml_element": "uses-permission",
                "new_content": '<uses-permission android:name="android.permission.INTERNET"/>',
            },
            0,
        )


def test_repeatable_manifest_element_requires_unique_name(provider):
    with pytest.raises(GeminiProviderError, match="select one manifest element"):
        provider._parse_patch_operation(
            {
                "relative_path": "AndroidManifest.xml",
                "operation": "manifest_update",
                "xml_element": "activity",
                "xml_attributes": {"android:label": "New label"},
            },
            0,
        )


def test_manifest_intent_filter_accepts_exact_prechange_selector(provider):
    operation = provider._parse_patch_operation(
        {
            "relative_path": "AndroidManifest.xml",
            "operation": "manifest_update",
            "xml_element": "intent-filter",
            "xml_match_attributes": {"android:label": "@string/launcher_name"},
            "xml_attributes": {"android:label": "NOIR Code Lab"},
        },
        0,
    )

    assert operation.xml_match_attributes == {"android:label": "@string/launcher_name"}
    assert operation.xml_attributes == {"android:label": "NOIR Code Lab"}


def test_empty_manifest_selector_does_not_change_legacy_patch_serialization():
    from noir.domain.models import PatchOperation

    operation = PatchOperation(
        relative_path="AndroidManifest.xml",
        operation="manifest_update",
        xml_element="application",
        xml_attributes={"android:label": "NOIR"},
    )

    assert "xml_match_attributes" not in operation.model_dump()
    assert "xml_match_attributes" not in operation.model_dump_json()


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


@pytest.mark.parametrize(
    "missing", ["class_descriptor", "method_signature", "new_content", "anchor"]
)
def test_smali_insert_requires_complete_operation_metadata(provider, missing):
    operation = {
        "relative_path": "A.smali",
        "operation": "smali_insert_at_anchor",
        "class_descriptor": "LA;",
        "method_signature": "newMethod()V",
        "new_content": ".method public newMethod()V\n.locals 0\nreturn-void\n.end method",
        "anchor": "# virtual methods",
    }
    operation.pop(missing)
    with pytest.raises(GeminiProviderError, match="requires|require"):
        provider._parse_patch_operation(operation, 0)


def test_ai_cannot_create_a_standalone_smali_payload(provider):
    with pytest.raises(GeminiProviderError, match="AI-generated Smali classes"):
        provider._parse_patch_operation(
            {
                "relative_path": "smali/in/v0id/Generated.smali",
                "operation": "create_file",
                "new_content": ".class public Lin/v0id/Generated;",
            },
            0,
        )


def test_ai_smali_bridge_has_a_strict_size_limit(provider):
    with pytest.raises(GeminiProviderError, match="4096-byte"):
        provider._parse_patch_operation(
            {
                "relative_path": "smali/example/App.smali",
                "operation": "smali_insert_at_anchor",
                "class_descriptor": "Lexample/App;",
                "method_signature": "onCreate()V",
                "anchor": "invoke-super {p0}, Landroid/app/Activity;->onCreate()V",
                "new_content": "#" * 4097,
            },
            0,
        )


def inject_sequence(monkeypatch, provider, responses):
    """Inject real SDK response objects, not a simulated production provider."""
    from google.genai import types

    pending = iter(responses)
    calls = []

    def generate_content(**kwargs):
        calls.append(kwargs)
        text, reason = next(pending)
        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    finish_reason=reason,
                    content=types.Content(parts=[types.Part(text=text)]),
                )
            ]
        )

    monkeypatch.setattr(
        provider,
        "_get_client",
        lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)),
    )
    return calls


@pytest.mark.parametrize("partial", ['{"new_content":"unfinished', '{"looks":"complete"}'])
def test_max_tokens_never_accepts_partial_response(monkeypatch, provider, partial, caplog):
    calls = inject_sequence(
        monkeypatch,
        provider,
        [
            (partial, "MAX_TOKENS"),
            ('{"status":"ok"}', "STOP"),
        ],
    )
    assert provider._call_model("original prompt", "original system") == '{"status":"ok"}'
    assert [call["config"].max_output_tokens for call in calls] == [16_384, 32_768]
    assert all(call["contents"] == "original prompt" for call in calls)
    assert all(call["config"].system_instruction == "original system" for call in calls)
    assert partial not in caplog.text
    assert "discarded response" in caplog.text


def test_malformed_json_regenerates_without_concatenating_fragments(monkeypatch, provider):
    calls = inject_sequence(
        monkeypatch,
        provider,
        [
            ('{"secret":"partial', "STOP"),
            ('{"status":"ok"}', "STOP"),
        ],
    )
    assert provider._call_model("original") == '{"status":"ok"}'
    assert [call["config"].max_output_tokens for call in calls] == [16_384, 16_384]
    assert all(call["contents"] == "original" for call in calls)


@pytest.mark.parametrize("retry_limit", [0, 1, 2])
@pytest.mark.parametrize("reason", ["STOP", "MAX_TOKENS"])
def test_response_retries_are_bounded_and_fail_closed(monkeypatch, provider, retry_limit, reason):
    provider.config.ai_response_retry_limit = retry_limit
    calls = inject_sequence(monkeypatch, provider, [("{", reason)] * (retry_limit + 1))
    with pytest.raises(GeminiProviderError, match="No partial response was used or applied"):
        provider._call_model("original")
    assert len(calls) == retry_limit + 1


@pytest.mark.parametrize("reason", ["SAFETY", "RECITATION", "OTHER", None])
def test_non_success_finish_reasons_are_not_retried(monkeypatch, provider, reason):
    calls = inject_sequence(monkeypatch, provider, [('{"status":"ok"}', reason)])
    with pytest.raises(GeminiProviderError, match="did not finish normally"):
        provider._call_model("original")
    assert len(calls) == 1


def test_prompt_block_is_not_retried(monkeypatch, provider):
    from google.genai import types

    calls = []

    def blocked(**kwargs):
        calls.append(kwargs)
        return types.GenerateContentResponse(
            prompt_feedback=types.GenerateContentResponsePromptFeedback(
                block_reason=types.BlockedReason.SAFETY,
            )
        )

    monkeypatch.setattr(
        provider,
        "_get_client",
        lambda: SimpleNamespace(models=SimpleNamespace(generate_content=blocked)),
    )
    with pytest.raises(GeminiProviderError, match="blocked this request"):
        provider._call_model("original")
    assert len(calls) == 1


def test_api_error_is_redacted_and_not_retried_as_json(monkeypatch, provider):
    calls = []

    def unavailable(**kwargs):
        calls.append(kwargs)
        raise RuntimeError(f"API failure with key {provider.api_key}")

    monkeypatch.setattr(
        provider,
        "_get_client",
        lambda: SimpleNamespace(models=SimpleNamespace(generate_content=unavailable)),
    )
    with pytest.raises(GeminiProviderError) as exc:
        provider._call_model("original")
    assert provider.api_key not in str(exc.value)
    assert "[REDACTED]" in str(exc.value)
    assert len(calls) == 1


def test_schema_counts_toward_request_budget_before_network(monkeypatch, provider):
    provider.config.ai_max_request_size = 30
    monkeypatch.setattr(provider, "_get_client", lambda: pytest.fail("Must not contact Gemini"))
    with pytest.raises(GeminiProviderError, match="No request was sent"):
        provider._call_model("test", response_schema={"type": "object", "required": ["हिन्दी"]})


def test_output_token_override_is_capped_on_retry(monkeypatch, provider):
    provider.max_output_tokens = 40_000
    calls = inject_sequence(monkeypatch, provider, [("{", "MAX_TOKENS"), ("{}", "STOP")])
    assert provider._call_model("test") == "{}"
    assert [call["config"].max_output_tokens for call in calls] == [40_000, 65_536]


def test_plain_text_summary_does_not_require_json(monkeypatch, provider):
    calls = inject_sequence(monkeypatch, provider, [("A complete summary.", "STOP")])
    assert provider.generate_summary({}) == "A complete summary."
    assert calls[0]["config"].response_mime_type == "text/plain"


def test_output_limits_load_from_environment(monkeypatch):
    monkeypatch.setenv("NOIR_AI_MAX_OUTPUT_TOKENS", "24576")
    monkeypatch.setenv("NOIR_AI_RESPONSE_RETRY_LIMIT", "0")
    config = NoirConfig(_env_file=None, gemini_api_key="unit-test-only")
    assert GeminiProvider(config=config).max_output_tokens == 24576
    assert config.ai_response_retry_limit == 0
    monkeypatch.setenv("NOIR_AI_RESPONSE_RETRY_LIMIT", "100")
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        NoirConfig(_env_file=None, gemini_api_key="unit-test-only")


def test_large_manifest_patch_retry_keeps_only_complete_operations(monkeypatch, provider, tmp_path):
    from noir.domain.enums import PatchOperationType
    from noir.domain.models import ChangePlan, PlanFileChange
    from noir.infrastructure.filesystem.workspace import ProjectWorkspace, compute_file_hash
    from noir.patches.engine import PatchEngine

    ws = ProjectWorkspace(
        "compact_patch", provider.config.model_copy(update={"data_dir": str(tmp_path)})
    )
    ws.create()
    manifest = ws.decoded_dir / "AndroidManifest.xml"
    strings = ws.decoded_dir / "res/values/strings.xml"
    strings.parent.mkdir(parents=True)
    tags = ['<application android:label="@string/app_name">'] + [
        f'<activity-alias android:label="@string/app_name" android:name="Launcher{i}" />'
        for i in range(6)
    ]
    original = (
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">'
        + tags[0]
        + "<!--"
        + "unrelated" * 4000
        + "-->"
        + "".join(tags[1:])
        + "</application></manifest>"
    )
    manifest.write_text(original)
    old_string = '<string name="app_name">Original</string>'
    strings.write_text(f"<resources>{old_string}</resources>")
    paths = ["AndroidManifest.xml", "res/values/strings.xml"]
    plan = ChangePlan(
        project_id=ws.project_id,
        user_request="Rename labels to a1b2c3",
        workspace_revision=0,
        file_changes=[
            PlanFileChange(relative_path=path, operation=PatchOperationType.REPLACE_BLOCK)
            for path in paths
        ],
    )
    operations = [
        {
            "relative_path": paths[0],
            "operation": "replace_block",
            "match_content": tag,
            "new_content": tag.replace("@string/app_name", "a1b2c3"),
        }
        for tag in tags
    ] + [
        {
            "relative_path": paths[1],
            "operation": "replace_block",
            "match_content": old_string,
            "new_content": '<string name="app_name">a1b2c3</string>',
        }
    ]
    complete = json.dumps({"operations": operations})
    calls = inject_sequence(
        monkeypatch,
        provider,
        [
            ('{"operations":[{"new_content":"incomplete', "MAX_TOKENS"),
            (complete, "STOP"),
        ],
    )
    patch = provider.generate_patch(
        plan,
        {
            "file_snippets": {path: (ws.decoded_dir / path).read_text() for path in paths},
            "file_hashes": {path: compute_file_hash(ws.decoded_dir / path) for path in paths},
        },
    )
    schema = calls[0]["config"].response_json_schema
    item = schema["properties"]["operations"]["items"]
    assert {
        (
            variant["properties"]["relative_path"]["enum"][0],
            variant["properties"]["operation"]["enum"][0],
        )
        for variant in item["oneOf"]
    } == {(path, "replace_block") for path in paths}
    assert all(
        "match_content" in variant["required"] and "new_content" in variant["required"]
        for variant in item["oneOf"]
    )
    assert all("expected_preimage_hash" not in variant["properties"] for variant in item["oneOf"])
    assert "shortest exact match_content" in calls[0]["contents"]
    assert len(complete.encode()) < 4000
    assert len(patch.operations) == 8
    assert manifest.read_text() == original  # Generation and retries never apply changes.
    engine = PatchEngine(ws)
    assert not engine.validate_patch(patch)
    engine.apply_patch(patch)
    assert manifest.read_text() == original.replace("@string/app_name", "a1b2c3")
    assert strings.read_text() == '<resources><string name="app_name">a1b2c3</string></resources>'
