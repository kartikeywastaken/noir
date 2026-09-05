"""OpenRouter discovery provider tests. No remote API calls."""

import json
from unittest.mock import patch

import pytest

from noir.domain.config import NoirConfig
from noir.domain.models import AnalysisResult
from noir.infrastructure.ai.context import AiContextTools
from noir.infrastructure.ai.discovery import DiscoveryResult, DiscoveryToolExecutor
from noir.infrastructure.ai.openrouter import (
    OpenRouterDiscoveryError,
    OpenRouterDiscoveryProvider,
    _build_analysis_summary,
    _openai_tool_definitions,
)
from noir.infrastructure.filesystem.workspace import ProjectWorkspace

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def config(tmp_path):
    return NoirConfig(
        _env_file=None,
        data_dir=str(tmp_path),
        gemini_api_key="test-only",
        openrouter_api_key="sk-or-test-key",
        discovery_provider="openrouter",
        openrouter_discovery_model="google/gemma-3-27b-it:free",
    )


@pytest.fixture
def ws(tmp_path, config):
    workspace = ProjectWorkspace("testopenrouter", config)
    workspace.create()
    manifest = (
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">'
        '<application android:label="TestApp">'
        "</application></manifest>"
    )
    (workspace.decoded_dir / "AndroidManifest.xml").write_text(manifest)
    return workspace


def _make_analysis(ws, **overrides):
    defaults = dict(project_id=ws.project_id)
    defaults.update(overrides)
    return AnalysisResult(**defaults)


# ── Tool definition format tests ─────────────────────────────────────


def test_openai_tool_definitions_have_correct_format():
    """Tool definitions should be in OpenAI function-calling format."""
    tools = _openai_tool_definitions()
    assert len(tools) == 4
    for tool in tools:
        assert tool["type"] == "function"
        assert "function" in tool
        func = tool["function"]
        assert "name" in func
        assert "description" in func
        assert "parameters" in func


def test_openai_tool_definitions_include_all_tools():
    """All four discovery tools should be present."""
    tools = _openai_tool_definitions()
    names = {tool["function"]["name"] for tool in tools}
    assert names == {"search_workspace", "list_directory", "read_file_excerpt", "inspect_binary"}


# ── Provider initialization tests ────────────────────────────────────


def test_provider_requires_api_key(tmp_path):
    """Provider should raise when no API key is configured."""
    config = NoirConfig(
        _env_file=None,
        data_dir=str(tmp_path),
        gemini_api_key="test-only",
        openrouter_api_key="",
    )
    with pytest.raises(OpenRouterDiscoveryError, match="API key not configured"):
        OpenRouterDiscoveryProvider(config)


def test_provider_accepts_valid_config(config):
    """Provider should initialize with valid config."""
    provider = OpenRouterDiscoveryProvider(config)
    assert provider.model == "google/gemma-3-27b-it:free"
    assert provider.timeout == 30


# ── Label task fast path ─────────────────────────────────────────────


def test_label_task_skips_discovery(config, ws):
    """Label/rename tasks should skip discovery entirely."""
    provider = OpenRouterDiscoveryProvider(config)
    analysis = _make_analysis(ws, runtimes={"dalvik"})
    context_tools = AiContextTools(ws, analysis)

    result = provider.discover("Rename app label to MyApp", context_tools, analysis)

    assert result.used_static_fallback is True
    assert result.stop_reason == "static_fast_path"
    assert result.api_calls == 0


# ── Discovery disabled test ──────────────────────────────────────────


def test_discovery_disabled_returns_static(config, ws):
    """Kill switch should disable discovery."""
    config.discovery_enabled = False
    provider = OpenRouterDiscoveryProvider(config)
    analysis = _make_analysis(ws, runtimes={"dalvik"})
    context_tools = AiContextTools(ws, analysis)

    result = provider.discover("give unlimited coins", context_tools, analysis)

    assert result.used_static_fallback is True
    assert result.stop_reason == "discovery_disabled"


# ── API response parsing tests ───────────────────────────────────────


def test_model_finished_response(config, ws):
    """When model returns no tool_calls, discovery should stop."""
    provider = OpenRouterDiscoveryProvider(config)
    analysis = _make_analysis(ws, runtimes={"dalvik"})
    context_tools = AiContextTools(ws, analysis)

    mock_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "I found the relevant files.",
                },
                "finish_reason": "stop",
            }
        ]
    }

    with patch.object(provider, "_call_api", return_value=mock_response):
        result = provider.discover("change the score", context_tools, analysis)

    assert result.stop_reason == "model_finished"
    assert result.used_static_fallback is True  # No files found
    assert result.api_calls == 1


def test_tool_call_response_reads_file(config, ws):
    """When model requests a file read, the file content should be captured."""
    # Create a test file
    target = ws.decoded_dir / "smali" / "com" / "game" / "Score.smali"
    target.parent.mkdir(parents=True)
    target.write_text(
        ".class public Lcom/game/Score;\n"
        ".super Ljava/lang/Object;\n"
        ".method public getScore()I\n"
        "    .registers 2\n"
        "    const v0, 0x64\n"
        "    return v0\n"
        ".end method\n"
    )

    provider = OpenRouterDiscoveryProvider(config)
    analysis = _make_analysis(ws, runtimes={"dalvik"})
    context_tools = AiContextTools(ws, analysis)

    # First call: model makes a tool call
    tool_call_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {
                                "name": "read_file_excerpt",
                                "arguments": json.dumps(
                                    {"path": "smali/com/game/Score.smali"}
                                ),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }

    # Second call lets the model assess the exact evidence it just received.
    done_response = {
        "choices": [
            {
                "message": {"role": "assistant", "content": "Found the score file."},
                "finish_reason": "stop",
            }
        ]
    }

    call_count = [0]

    def mock_call_api(messages, tools):
        call_count[0] += 1
        if call_count[0] == 1:
            return tool_call_response
        return done_response

    with patch.object(provider, "_call_api", side_effect=mock_call_api):
        result = provider.discover("change the score", context_tools, analysis)

    assert "smali/com/game/Score.smali" in result.seen_files
    assert "getScore" in result.seen_files["smali/com/game/Score.smali"]
    assert result.stop_reason == "model_finished"
    assert result.used_static_fallback is False
    assert result.api_calls == 2


def test_api_failure_falls_back_to_static(config, ws):
    """API failures should fall back to static selection."""
    provider = OpenRouterDiscoveryProvider(config)
    analysis = _make_analysis(ws, runtimes={"dalvik"})
    context_tools = AiContextTools(ws, analysis)

    with patch.object(provider, "_call_api", side_effect=Exception("Network error")):
        result = provider.discover("change something", context_tools, analysis)

    assert result.used_static_fallback is True
    assert result.stop_reason == "discovery_error"


# ── Tool executor tests ──────────────────────────────────────────────


def test_tool_executor_search(ws):
    """DiscoveryToolExecutor.execute dispatches search correctly."""
    target = ws.decoded_dir / "smali" / "Test.smali"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("coins balance check")

    analysis = _make_analysis(ws, runtimes={"dalvik"})
    executor = DiscoveryToolExecutor(AiContextTools(ws, analysis))
    result = DiscoveryResult()

    output, summary, nbytes = executor.execute("search_workspace", {"query": "coins"}, result)
    assert "coins" in output.lower() or nbytes > 0


def test_tool_executor_read(ws):
    """DiscoveryToolExecutor.execute dispatches file reads correctly."""
    target = ws.decoded_dir / "test_file.xml"
    target.write_text("<resources/>")

    analysis = _make_analysis(ws, runtimes={"dalvik"})
    executor = DiscoveryToolExecutor(AiContextTools(ws, analysis))
    result = DiscoveryResult()

    output, summary, nbytes = executor.execute(
        "read_file_excerpt", {"path": "test_file.xml"}, result
    )
    assert "test_file.xml" in result.seen_files
    assert result.seen_files["test_file.xml"] == "<resources/>"


def test_tool_executor_unknown_tool(ws):
    """Unknown tool names should return an error, not raise."""
    analysis = _make_analysis(ws, runtimes={"dalvik"})
    executor = DiscoveryToolExecutor(AiContextTools(ws, analysis))
    result = DiscoveryResult()

    output, summary, nbytes = executor.execute("nonexistent_tool", {}, result)
    assert "Unknown tool" in output
    assert nbytes == 0


# ── Analysis summary tests ───────────────────────────────────────────


def test_analysis_summary_structure():
    """Analysis summary should contain expected fields."""
    analysis = AnalysisResult(
        project_id="test",
        package_name="com.example.app",
        runtimes={"dalvik", "native"},
    )
    summary = _build_analysis_summary(analysis)
    assert summary["package_name"] == "com.example.app"
    assert "dalvik" in summary["runtimes"]
    assert "native" in summary["runtimes"]
    assert "smali_class_count" in summary
    assert "binary_candidates" in summary


# ── Cache dedup test ─────────────────────────────────────────────────


def test_duplicate_tool_calls_are_cached(config, ws):
    """Identical tool calls within a discovery run should be deduplicated."""
    target = ws.decoded_dir / "smali" / "Dup.smali"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("duplicate test content")

    provider = OpenRouterDiscoveryProvider(config)
    config.discovery_max_rounds = 2  # Allow 2 rounds
    analysis = _make_analysis(ws, runtimes={"dalvik"})
    context_tools = AiContextTools(ws, analysis)

    # Both rounds make the same tool call
    tool_call_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_dup",
                            "type": "function",
                            "function": {
                                "name": "search_workspace",
                                "arguments": json.dumps({"query": "duplicate"}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }

    with patch.object(provider, "_call_api", return_value=tool_call_response):
        result = provider.discover("find duplicate content", context_tools, analysis)

    assert result.api_calls == 2
    # Second call should be cached (0 bytes charged)
    assert len(result.transcript) == 2
    assert result.transcript[1].bytes_returned == 0
    assert result.transcript[1].result_summary.startswith("Cached:")
