"""Google ADK adapter tests. No remote model calls."""

from types import SimpleNamespace

import pytest

from noir.domain.config import NoirConfig
from noir.domain.enums import PatchOperationType
from noir.domain.models import AnalysisResult, ChangePlan, PlanFileChange
from noir.infrastructure.ai.adk import (
    AdkDiscoveryProvider,
    AdkGeminiProvider,
    _final_text,
)
from noir.infrastructure.ai.context import AiContextTools
from noir.infrastructure.ai.gemini import GeminiProviderError
from noir.infrastructure.filesystem.workspace import ProjectWorkspace


def _config(tmp_path, **overrides):
    values = {
        "_env_file": None,
        "data_dir": str(tmp_path),
        "gemini_api_key": "unit-test-only",
        "ai_provider": "adk",
        "discovery_provider": "adk",
        "ai_model": "gemini-3.6-flash",
        "ai_response_retry_limit": 0,
    }
    values.update(overrides)
    return NoirConfig(**values)


def test_adk_generation_uses_textual_schema_for_gemini_36(tmp_path, monkeypatch):
    provider = AdkGeminiProvider(config=_config(tmp_path))
    captured = {}

    def run_agent(**kwargs):
        captured.update(kwargs)
        return '{"status":"ok"}'

    monkeypatch.setattr(provider, "_run_agent", run_agent)
    schema = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
    }

    assert provider._call_model("test", response_schema=schema) == '{"status":"ok"}'
    assert captured["response_schema"] is None
    assert '"required":["status"]' in captured["prompt"]


def test_adk_generation_preserves_configured_model_fallback(tmp_path, monkeypatch):
    provider = AdkGeminiProvider(
        config=_config(
            tmp_path,
            ai_model="gemini-3.7-flash",
            ai_fallback_model="gemini-3.6-flash",
        )
    )
    calls = []

    class UnavailableError(Exception):
        status_code = 503

    def run_agent(**kwargs):
        calls.append(kwargs["model_name"])
        if kwargs["model_name"] == "gemini-3.7-flash":
            raise UnavailableError("503 UNAVAILABLE")
        return '{"status":"ok"}'

    monkeypatch.setattr(provider, "_run_agent", run_agent)

    assert provider._call_model("test") == '{"status":"ok"}'
    assert calls == ["gemini-3.7-flash", "gemini-3.6-flash"]
    assert provider.last_model_name == "gemini-3.6-flash"


def test_adk_final_event_surfaces_provider_error():
    event = SimpleNamespace(
        is_final_response=lambda: True,
        error_code="ServerError",
        error_message="503 UNAVAILABLE",
        content=None,
        output=None,
    )

    with pytest.raises(GeminiProviderError, match="503 UNAVAILABLE"):
        _final_text([event])


def test_adk_task_mode_reads_finish_task_output():
    event = SimpleNamespace(
        is_final_response=lambda: False,
        error_code=None,
        error_message=None,
        content=None,
        output={"result": '{"status":"ok"}'},
    )

    assert _final_text([event]) == '{"status":"ok"}'


def test_adk_agent_writes_concrete_patch_operations(tmp_path, monkeypatch):
    provider = AdkGeminiProvider(config=_config(tmp_path))
    monkeypatch.setattr(
        provider,
        "_run_agent",
        lambda **kwargs: (
            '{"operations":[{"relative_path":"res/values/strings.xml",'
            '"operation":"replace_block","match_content":"Chess",'
            '"new_content":"NOIR Chess"}]}'
        ),
    )
    plan = ChangePlan(
        project_id="patch-test",
        workspace_revision=0,
        user_request="Rename the title",
        intended_outcome="Rename the title",
        file_changes=[
            PlanFileChange(
                relative_path="res/values/strings.xml",
                operation=PatchOperationType.REPLACE_BLOCK,
            )
        ],
    )

    patch = provider.generate_patch(
        plan,
        {
            "file_snippets": {"res/values/strings.xml": "<string>Chess</string>"},
            "file_hashes": {"res/values/strings.xml": "a" * 64},
        },
    )

    assert patch.operations[0].new_content == "NOIR Chess"
    assert patch.operations[0].expected_preimage_hash == "a" * 64


def test_ai_factory_selects_adk_without_changing_provider_interface(tmp_path):
    from noir.infrastructure.ai.factory import create_ai_provider

    provider = create_ai_provider(_config(tmp_path))

    assert isinstance(provider, AdkGeminiProvider)


def test_adk_discovery_returns_host_grounded_tool_evidence(tmp_path, monkeypatch):
    config = _config(tmp_path)
    workspace = ProjectWorkspace("adkdiscovery", config)
    workspace.create()
    target = workspace.decoded_dir / "res" / "values" / "strings.xml"
    target.parent.mkdir(parents=True)
    target.write_text('<resources><string name="title">Chess</string></resources>')
    provider = AdkDiscoveryProvider(config)

    def run_discovery_agent(**kwargs):
        read_file_excerpt = kwargs["tools"][2]
        read_file_excerpt("res/values/strings.xml", 0, 10_000)
        kwargs["result"].stop_reason = "model_finished"

    monkeypatch.setattr(provider, "_run_discovery_agent", run_discovery_agent)
    result = provider.discover(
        "change the title shown inside the app",
        AiContextTools(workspace),
        AnalysisResult(project_id=workspace.project_id, package_name="example.chess"),
    )

    assert result.used_static_fallback is False
    assert result.seen_files["res/values/strings.xml"] == target.read_text()
    assert result.transcript[0].tool_name == "read_file_excerpt"
