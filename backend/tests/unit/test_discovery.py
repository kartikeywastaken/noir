"""Discovery, runtimes, and evidence-gating tests. No remote AI calls."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from noir.application.patch_service import PlanServiceError
from noir.domain.config import NoirConfig
from noir.domain.models import AnalysisResult, NativeLibInfo
from noir.infrastructure.ai.context import AiContextTools
from noir.infrastructure.filesystem.workspace import ProjectWorkspace

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def ws(tmp_path):
    config = NoirConfig(_env_file=None, data_dir=str(tmp_path), gemini_api_key="test-only")
    workspace = ProjectWorkspace("testdiscovery", config)
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


# ── Phase B: runtimes set tests ──────────────────────────────────────


def test_runtimes_set_contains_multiple_members_for_dalvik_plus_native(ws):
    """A Dalvik app with JNI native libraries must get both 'dalvik' and 'native'."""
    from noir.analysis.analyzer import AnalysisService

    # Create a minimal workspace with both smali and native libs
    smali_dir = ws.decoded_dir / "smali" / "com" / "example"
    smali_dir.mkdir(parents=True)
    smali_file = smali_dir / "GameManager.smali"
    smali_file.write_text(
        ".class public Lcom/example/GameManager;\n"
        ".super Ljava/lang/Object;\n"
        ".method public getCoinBalance()I\n"
        "    .registers 2\n"
        "    const/4 v0, 0x0\n"
        "    return v0\n"
        ".end method\n"
    )

    lib_dir = ws.decoded_dir / "lib" / "arm64-v8a"
    lib_dir.mkdir(parents=True)
    (lib_dir / "libnative.so").write_bytes(b"\x7fELF" + b"\x00" * 100)

    service = AnalysisService(ws.config)
    result = service.analyze(ws.project_id, ws, persist=False)

    assert "dalvik" in result.runtimes
    assert "native" in result.runtimes
    # The old code would leave runtime == "dalvik" and skip native evidence
    assert result.native_libs  # verify native libs were detected


def test_hybrid_web_detection_cordova(ws):
    """assets/www/index.html correctly adds 'hybrid_web' to runtimes."""
    from noir.analysis.analyzer import AnalysisService

    # Create Cordova-style structure
    www_dir = ws.decoded_dir / "assets" / "www"
    www_dir.mkdir(parents=True)
    (www_dir / "index.html").write_text("<html><body>Cordova App</body></html>")

    smali_dir = ws.decoded_dir / "smali" / "com" / "example"
    smali_dir.mkdir(parents=True)
    (smali_dir / "Main.smali").write_text(
        ".class public Lcom/example/Main;\n.super Ljava/lang/Object;\n"
    )

    service = AnalysisService(ws.config)
    result = service.analyze(ws.project_id, ws, persist=False)

    assert "hybrid_web" in result.runtimes
    assert "dalvik" in result.runtimes  # doesn't disturb dalvik


def test_react_native_bundle_is_not_mislabeled_as_hybrid_web(ws):
    """A Metro JavaScript bundle identifies React Native, not a WebView wrapper."""
    from noir.analysis.analyzer import AnalysisService

    assets_dir = ws.decoded_dir / "assets"
    assets_dir.mkdir(parents=True)
    (assets_dir / "index.android.bundle").write_text("__d(function(){...})")

    smali_dir = ws.decoded_dir / "smali" / "com" / "example"
    smali_dir.mkdir(parents=True)
    (smali_dir / "Main.smali").write_text(
        ".class public Lcom/example/Main;\n.super Ljava/lang/Object;\n"
    )

    service = AnalysisService(ws.config)
    result = service.analyze(ws.project_id, ws, persist=False)

    assert "react_native" in result.runtimes
    assert "hybrid_web" not in result.runtimes
    assert result.runtime_evidence["react_native"] == ["assets/index.android.bundle"]
    assert result.primary_runtime == "react_native"


def test_hermes_bytecode_and_react_native_libraries_are_detected_additively(ws):
    """Hermes HBC and RN native markers retain all applicable runtime capabilities."""
    from noir.analysis.analyzer import AnalysisService

    assets_dir = ws.decoded_dir / "assets"
    assets_dir.mkdir(parents=True)
    hermes_magic = (0x1F1903C103BC1FC6).to_bytes(8, byteorder="little")
    (assets_dir / "index.android.bundle").write_bytes(hermes_magic + b"\x00" * 64)

    lib_dir = ws.decoded_dir / "lib" / "arm64-v8a"
    lib_dir.mkdir(parents=True)
    (lib_dir / "libreactnative.so").write_bytes(b"\x7fELF" + b"\x00" * 100)
    (lib_dir / "libhermes.so").write_bytes(b"\x7fELF" + b"\x00" * 100)

    smali_dir = ws.decoded_dir / "smali" / "com" / "example"
    smali_dir.mkdir(parents=True)
    (smali_dir / "Main.smali").write_text(
        ".class public Lcom/example/Main;\n.super Ljava/lang/Object;\n"
    )

    result = AnalysisService(ws.config).analyze(ws.project_id, ws, persist=False)

    assert {"dalvik", "native", "react_native", "hermes"} <= result.runtimes
    assert "hybrid_web" not in result.runtimes
    assert result.primary_runtime == "hermes"
    assert result.runtime_evidence["hermes_bytecode"] == ["assets/index.android.bundle"]
    assert "assets/index.android.bundle" in result.runtime_evidence["hermes"]
    assert "lib/arm64-v8a/libhermes.so" in result.runtime_evidence["hermes"]
    assert "lib/arm64-v8a/libreactnative.so" in result.runtime_evidence["react_native"]


def test_flutter_detection_uses_runtime_library_and_assets(ws):
    """A Flutter package is classified separately from a generic JNI app."""
    from noir.analysis.analyzer import AnalysisService

    flutter_assets = ws.decoded_dir / "assets" / "flutter_assets"
    flutter_assets.mkdir(parents=True)
    (flutter_assets / "AssetManifest.bin").write_bytes(b"flutter-assets")

    lib_dir = ws.decoded_dir / "lib" / "arm64-v8a"
    lib_dir.mkdir(parents=True)
    (lib_dir / "libflutter.so").write_bytes(b"\x7fELF" + b"\x00" * 100)

    smali_dir = ws.decoded_dir / "smali" / "com" / "example"
    smali_dir.mkdir(parents=True)
    (smali_dir / "Main.smali").write_text(
        ".class public Lcom/example/Main;\n.super Ljava/lang/Object;\n"
    )

    result = AnalysisService(ws.config).analyze(ws.project_id, ws, persist=False)

    assert {"dalvik", "native", "flutter"} <= result.runtimes
    assert result.primary_runtime == "flutter"


def test_ad_sdk_webview_reference_does_not_make_app_hybrid(ws):
    """A lone WebView signature without bundled web content is not a hybrid runtime."""
    from noir.analysis.analyzer import AnalysisService

    smali_dir = ws.decoded_dir / "smali" / "com" / "example"
    smali_dir.mkdir(parents=True)
    (smali_dir / "Ads.smali").write_text(
        ".class public Lcom/example/Ads;\n"
        ".super Ljava/lang/Object;\n"
        ".method public configure(Landroid/webkit/WebView;)V\n"
        "    .registers 2\n"
        "    return-void\n"
        ".end method\n"
    )

    result = AnalysisService(ws.config).analyze(ws.project_id, ws, persist=False)

    assert "dalvik" in result.runtimes
    assert "hybrid_web" not in result.runtimes


def test_primary_runtime_property():
    """primary_runtime returns the highest-priority runtime from the set."""
    result = AnalysisResult(project_id="test", runtimes={"dalvik", "mono", "native"})
    assert result.primary_runtime == "mono"

    result2 = AnalysisResult(project_id="test", runtimes={"dalvik", "il2cpp", "native"})
    assert result2.primary_runtime == "il2cpp"

    result3 = AnalysisResult(project_id="test", runtimes={"dalvik"})
    assert result3.primary_runtime == "dalvik"

    result_rn = AnalysisResult(
        project_id="react-native",
        runtimes={"dalvik", "native", "react_native", "hermes"},
    )
    assert result_rn.primary_runtime == "hermes"

    # Legacy data with empty runtimes set falls back to runtime field
    result4 = AnalysisResult(project_id="test", runtime="mono")
    assert result4.primary_runtime == "mono"


# ── Phase B: evidence gating fix ─────────────────────────────────────


def test_native_evidence_triggers_on_actual_native_libs_not_runtime_label(ws, monkeypatch):
    """Dalvik+JNI app (runtime='dalvik' in old code) now gets native inspection."""
    relative = "lib/arm64-v8a/libnative.so"
    target = ws.decoded_dir / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\x7fELF" + b"\x00" * 100)

    # Create an analysis that has native_libs but would have had runtime="dalvik" under old logic
    analysis = _make_analysis(
        ws,
        runtimes={"dalvik", "native"},
        runtime="dalvik",
        native_libs=[NativeLibInfo(abi="arm64-v8a", libraries=["libnative.so"])],
        native_abis=["arm64-v8a"],
    )

    tools = AiContextTools(ws, analysis)
    monkeypatch.setattr(
        tools,
        "_inspect_binary_path",
        lambda path, *, user_request="": {
            "format": "elf",
            "path": path,
        },
    )

    context = tools.build_context(user_request="patch native crypto check")

    # Under the old code, this would have been skipped because runtime == "dalvik"
    assert relative in context["binary_inspection"]
    assert context["file_coverage"][relative] == "structured_binary_inspection"


# ── Phase C: discovery loop tests ────────────────────────────────────


def test_discovery_label_task_uses_zero_rounds(ws):
    """Label-rename fast path skips discovery entirely."""
    from noir.infrastructure.ai.discovery import EvidenceDiscovery

    analysis = _make_analysis(ws, runtimes={"dalvik"})
    context_tools = AiContextTools(ws, analysis)

    mock_provider = MagicMock()
    discovery = EvidenceDiscovery(mock_provider, context_tools, ws.config, analysis)
    result = discovery.discover("Rename app label to MyApp")

    assert result.used_static_fallback is True
    assert result.transcript == []
    mock_provider._get_client.assert_not_called()


def test_discovery_disabled_falls_back_to_static(ws):
    """Kill switch disables discovery, falls back to static selection."""
    from noir.infrastructure.ai.discovery import EvidenceDiscovery

    ws.config.discovery_enabled = False
    analysis = _make_analysis(ws, runtimes={"dalvik"})
    context_tools = AiContextTools(ws, analysis)

    mock_provider = MagicMock()
    discovery = EvidenceDiscovery(mock_provider, context_tools, ws.config, analysis)
    result = discovery.discover("give unlimited coins")

    assert result.used_static_fallback is True
    assert result.transcript == []


def test_discovery_budget_exhaustion_falls_back(ws):
    """Tiny budget exhaustion → graceful fallback, not error."""
    from noir.infrastructure.ai.discovery import EvidenceDiscovery

    ws.config.discovery_max_rounds = 1
    ws.config.ai_max_request_size = 100  # Tiny budget
    analysis = _make_analysis(ws, runtimes={"dalvik"})
    context_tools = AiContextTools(ws, analysis)

    mock_provider = MagicMock()
    # Simulate discovery_turn raising due to budget
    mock_provider._get_client.side_effect = Exception("Budget test")

    discovery = EvidenceDiscovery(mock_provider, context_tools, ws.config, analysis)
    result = discovery.discover("give unlimited coins")

    assert result.used_static_fallback is True


def test_discovery_happy_path_content_match_not_path_keyword(ws):
    """Discovery finds a file whose *content* matches, even when path doesn't contain keywords.

    This is THE regression test for the original bug.
    """
    from noir.infrastructure.ai.discovery import (
        DiscoveryResult,
        ToolCallRecord,
        build_discovered_context,
    )

    # The file's path is generic — no "coins" in the name
    economy_file = ws.decoded_dir / "smali" / "com" / "obfuscated" / "a.smali"
    economy_file.parent.mkdir(parents=True)
    economy_file.write_text(
        ".class public Lcom/obfuscated/a;\n"
        ".super Ljava/lang/Object;\n"
        ".method public getCoinBalance()I\n"
        "    .registers 2\n"
        "    const v0, 0x3e8\n"
        "    return v0\n"
        ".end method\n"
    )

    # Simulate what discovery would produce: the model searched for "coin" and found this file
    discovery = DiscoveryResult(
        seen_files={
            "smali/com/obfuscated/a.smali": economy_file.read_text(),
        },
        transcript=[
            ToolCallRecord(
                tool_name="search_workspace",
                arguments={"query": "coin"},
                bytes_returned=150,
                result_summary="Found 1 match in 1 file",
            ),
            ToolCallRecord(
                tool_name="read_file_excerpt",
                arguments={"path": "smali/com/obfuscated/a.smali"},
                bytes_returned=200,
                result_summary="Read smali/com/obfuscated/a.smali",
            ),
        ],
        used_static_fallback=False,
    )

    analysis = _make_analysis(ws, runtimes={"dalvik"})
    context_tools = AiContextTools(ws, analysis)

    context = build_discovered_context(
        context_tools, discovery, user_request="give unlimited coins"
    )

    # The file that the static ranker would never find (no "coins" in its path)
    # is now in the context via discovery
    assert "smali/com/obfuscated/a.smali" in context["file_snippets"]
    assert "getCoinBalance" in context["file_snippets"]["smali/com/obfuscated/a.smali"]
    assert context["file_coverage"]["smali/com/obfuscated/a.smali"] == "full"

    # MAX_FILES cap still applies
    assert (
        len(context["file_snippets"]) + len(context["binary_inspection"])
        <= AiContextTools.MAX_FILES
    )


def test_discovered_context_respects_max_files(ws):
    """Discovery can look at many files, but the final context obeys MAX_FILES."""
    from noir.infrastructure.ai.discovery import DiscoveryResult, build_discovered_context

    # Create more files than MAX_FILES
    for i in range(AiContextTools.MAX_FILES + 10):
        f = ws.decoded_dir / f"file_{i:03d}.smali"
        f.write_text(f"content of file {i}")

    seen = {
        f"file_{i:03d}.smali": f"content of file {i}" for i in range(AiContextTools.MAX_FILES + 10)
    }

    discovery = DiscoveryResult(seen_files=seen, used_static_fallback=False)
    analysis = _make_analysis(ws, runtimes={"dalvik"})
    context_tools = AiContextTools(ws, analysis)

    context = build_discovered_context(context_tools, discovery, user_request="test")

    total = len(context["file_snippets"]) + len(context["binary_inspection"])
    assert total <= AiContextTools.MAX_FILES
    assert len(context["omitted_files"]) > 0


# ── Audit trail tests ────────────────────────────────────────────────


def test_audit_report_includes_discovery_transcript():
    """Plan with discovery transcript appears in audit report."""
    from noir.domain.models import ChangePlan

    plan = ChangePlan(
        project_id="test",
        workspace_revision=0,
        user_request="give unlimited coins",
        discovery_transcript=[
            {
                "tool_name": "search_workspace",
                "arguments": {"query": "coin"},
                "bytes_returned": 500,
                "result_summary": "Found 3 matches in 2 files",
            },
            {
                "tool_name": "read_file_excerpt",
                "arguments": {"path": "smali/com/game/Economy.smali"},
                "bytes_returned": 1200,
                "result_summary": "Read Economy.smali",
            },
        ],
    )

    # The plan should serialize its transcript
    dumped = plan.model_dump()
    assert len(dumped["discovery_transcript"]) == 2
    assert dumped["discovery_transcript"][0]["tool_name"] == "search_workspace"


def test_audit_report_shows_static_when_no_discovery():
    """Plan without discovery transcript correctly indicates static selection."""
    from noir.domain.models import ChangePlan

    plan = ChangePlan(
        project_id="test",
        workspace_revision=0,
        user_request="rename app",
        discovery_transcript=[],
    )

    dumped = plan.model_dump()
    assert dumped["discovery_transcript"] == []


def test_tool_call_record_serialization():
    """ToolCallRecord.to_dict() produces the expected shape."""
    from noir.infrastructure.ai.discovery import ToolCallRecord

    record = ToolCallRecord(
        tool_name="search_workspace",
        arguments={"query": "coin", "glob": "*.smali"},
        bytes_returned=1500,
        result_summary="Found 5 matches",
    )
    d = record.to_dict()
    assert d["tool_name"] == "search_workspace"
    assert d["arguments"]["query"] == "coin"
    assert d["bytes_returned"] == 1500
    assert d["result_summary"] == "Found 5 matches"


def test_discovery_budget_tracking():
    """Budget tracks model requests independently from batched tool calls."""
    from noir.infrastructure.ai.discovery import DiscoveryBudget

    budget = DiscoveryBudget(max_rounds=3, max_total_bytes=1000)
    assert budget.has_room()
    assert budget.has_room(500)
    assert budget.has_room(1000)
    assert not budget.has_room(1001)

    budget.begin_round()
    budget.consume_bytes(400)
    assert budget.rounds_used == 1
    assert budget.bytes_consumed == 400
    assert budget.has_room(600)
    assert not budget.has_room(601)

    # Multiple tool results in the same model turn do not spend extra rounds.
    budget.consume_bytes(300)
    budget.consume_bytes(200)
    assert budget.rounds_used == 1
    budget.begin_round()
    budget.begin_round()
    assert budget.rounds_used == 3
    assert not budget.has_room()  # max_rounds reached


def test_exact_source_evidence_stops_discovery_early(ws):
    """A source read is enough for planning; no summary-only AI turn is needed."""
    from noir.infrastructure.ai.discovery import DiscoveryResult, EvidenceDiscovery

    result = DiscoveryResult(seen_files={"smali/com/example/Game.smali": ".class X"})
    assert EvidenceDiscovery._has_exact_evidence(result, "change the score") is True


def test_search_lead_alone_does_not_stop_discovery(ws):
    """Search/list leads must be followed by an exact file read or inspection."""
    from noir.infrastructure.ai.discovery import DiscoveryResult, EvidenceDiscovery

    result = DiscoveryResult()
    assert EvidenceDiscovery._has_exact_evidence(result, "change the score") is False


def test_manifest_read_only_stops_for_manifest_request(ws):
    from noir.infrastructure.ai.discovery import DiscoveryResult, EvidenceDiscovery

    result = DiscoveryResult(seen_files={"AndroidManifest.xml": "<manifest/>"})
    assert EvidenceDiscovery._has_exact_evidence(result, "add a permission") is True
    assert EvidenceDiscovery._has_exact_evidence(result, "change game logic") is False


def test_discovery_stops_after_one_api_call_when_exact_file_is_read(ws):
    """A batched exact read avoids an unnecessary model-summary request."""
    from noir.infrastructure.ai.discovery import EvidenceDiscovery

    target = ws.decoded_dir / "smali" / "com" / "example" / "Game.smali"
    target.parent.mkdir(parents=True)
    target.write_text(".class public Lcom/example/Game;\n")

    call = SimpleNamespace(
        name="read_file_excerpt",
        args={"path": "smali/com/example/Game.smali"},
    )
    content = SimpleNamespace(parts=[SimpleNamespace(function_call=call)])
    response = SimpleNamespace(candidates=[SimpleNamespace(content=content)])
    client = MagicMock()
    client.models.generate_content.return_value = response
    provider = MagicMock(model_name="gemini-test")
    provider._get_client.return_value = client
    analysis = _make_analysis(
        ws,
        runtimes={"dalvik", "native", "react_native", "hermes"},
        runtime_evidence={
            "react_native": ["assets/index.android.bundle"],
            "hermes_bytecode": ["assets/index.android.bundle"],
        },
    )

    result = EvidenceDiscovery(
        provider, AiContextTools(ws, analysis), ws.config, analysis
    ).discover("change the score calculation")

    assert result.api_calls == 1
    assert result.stop_reason == "exact_evidence_found"
    assert result.used_static_fallback is False
    client.models.generate_content.assert_called_once()
    request = client.models.generate_content.call_args.kwargs
    initial_prompt = request["contents"][0].parts[0].text
    assert '"hermes_bytecode"' in initial_prompt
    assert "compiled Hermes bytecode" in initial_prompt


def test_discovery_never_exceeds_two_api_calls_and_caches_duplicate_tools(ws):
    """Search-only conversations stop at the production cap without duplicate I/O."""
    from noir.infrastructure.ai.discovery import EvidenceDiscovery

    # Explicitly set 2 rounds to test the caching behavior across multiple rounds.
    ws.config.discovery_max_rounds = 2

    target = ws.decoded_dir / "smali" / "Searchable.smali"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("needle")
    call = SimpleNamespace(name="search_workspace", args={"query": "needle", "glob": "*.smali"})
    content = SimpleNamespace(parts=[SimpleNamespace(function_call=call)])
    response = SimpleNamespace(candidates=[SimpleNamespace(content=content)])
    client = MagicMock()
    client.models.generate_content.return_value = response
    provider = MagicMock(model_name="gemini-test")
    provider._get_client.return_value = client
    analysis = _make_analysis(ws, runtimes={"dalvik"})

    result = EvidenceDiscovery(
        provider, AiContextTools(ws, analysis), ws.config, analysis
    ).discover("change the score calculation")

    assert result.api_calls == 2
    assert result.stop_reason == "budget_exhausted"
    assert result.used_static_fallback is True
    assert client.models.generate_content.call_count == 2
    assert result.transcript[1].bytes_returned == 0
    assert result.transcript[1].result_summary.startswith("Cached:")


def test_search_workspace_tool_dispatch(ws):
    """search_workspace tool correctly dispatches to workspace.search_text."""
    from noir.infrastructure.ai.discovery import DiscoveryResult, EvidenceDiscovery

    # Create a file with content that matches
    target = ws.decoded_dir / "smali" / "com" / "game" / "Economy.smali"
    target.parent.mkdir(parents=True)
    target.write_text("const v0, 0x3e8  ; 1000 coins initial balance")

    analysis = _make_analysis(ws, runtimes={"dalvik"})
    context_tools = AiContextTools(ws, analysis)
    mock_provider = MagicMock()
    engine = EvidenceDiscovery(mock_provider, context_tools, ws.config, analysis)

    result = DiscoveryResult()
    output, summary, nbytes = engine._execute_tool("search_workspace", {"query": "coins"}, result)

    assert "coins" in output.lower() or "coin" in output.lower()
    assert nbytes > 0
    assert "match" in summary.lower() or "found" in summary.lower()


def test_read_file_tool_dispatch(ws):
    """read_file_excerpt tool correctly reads and records the file."""
    from noir.infrastructure.ai.discovery import DiscoveryResult, EvidenceDiscovery

    target = ws.decoded_dir / "test_read.smali"
    target.write_text("test content for reading")

    analysis = _make_analysis(ws, runtimes={"dalvik"})
    context_tools = AiContextTools(ws, analysis)
    mock_provider = MagicMock()
    engine = EvidenceDiscovery(mock_provider, context_tools, ws.config, analysis)

    result = DiscoveryResult()
    output, summary, nbytes = engine._execute_tool(
        "read_file_excerpt", {"path": "test_read.smali"}, result
    )

    assert "test_read.smali" in result.seen_files
    assert result.seen_files["test_read.smali"] == "test content for reading"
    assert nbytes > 0


# ── Workflow call budget tests ────────────────────────────────────────


def test_workflow_budget_raises_on_overflow():
    """Budget enforces ai_max_workflow_calls limit."""
    from noir.application.ai_service import _WorkflowCallBudget

    config = NoirConfig(
        _env_file=None,
        data_dir=str(Path.cwd()),
        gemini_api_key="test",
        ai_max_workflow_calls=3,
    )
    budget = _WorkflowCallBudget(config)
    budget.consume(2, label="discovery")
    budget.consume(1, label="plan")
    with pytest.raises(PlanServiceError, match="Workflow call budget exhausted"):
        budget.consume(1, label="correction")


def test_workflow_budget_allows_within_limit():
    """Budget doesn't raise when within limit."""
    from noir.application.ai_service import _WorkflowCallBudget

    config = NoirConfig(
        _env_file=None,
        data_dir=str(Path.cwd()),
        gemini_api_key="test",
        ai_max_workflow_calls=10,
    )
    budget = _WorkflowCallBudget(config)
    budget.consume(3, label="discovery")
    budget.consume(1, label="plan")
    budget.consume(1, label="correction")
    assert budget.used == 5


# ── Plan cache tests ─────────────────────────────────────────────────


def test_plan_cache_hit_and_miss():
    """Cache returns stored plan on exact match, None on any mismatch."""
    from noir.application.ai_service import _PlanCache

    cache = _PlanCache()
    plan = {"fake": "plan"}
    cache.put("proj1", 5, "give unlimited coins", plan)

    # Exact match → hit
    assert cache.get("proj1", 5, "give unlimited coins") is plan

    # Different request → miss
    assert cache.get("proj1", 5, "rename app") is None

    # Different revision → miss
    assert cache.get("proj1", 6, "give unlimited coins") is None

    # Different project → miss
    assert cache.get("proj2", 5, "give unlimited coins") is None


def test_plan_cache_evicts_old_entries():
    """Cache evicts oldest entries when over limit."""
    from noir.application.ai_service import _PLAN_CACHE_MAX, _PlanCache

    cache = _PlanCache()
    for i in range(_PLAN_CACHE_MAX + 5):
        cache.put(f"proj{i}", 0, "request", {"plan": i})

    # First 5 entries should be evicted
    for i in range(5):
        assert cache.get(f"proj{i}", 0, "request") is None

    # Later entries should still be present
    for i in range(5, _PLAN_CACHE_MAX + 5):
        assert cache.get(f"proj{i}", 0, "request") is not None


# ── Retry logic tests ────────────────────────────────────────────────


def test_quota_error_not_retryable_without_fallback():
    """429/RESOURCE_EXHAUSTED is not retryable when no fallback model configured."""
    from noir.infrastructure.ai.gemini import _is_retryable_availability_error

    # Simulated quota error
    quota_exc = Exception("429 RESOURCE_EXHAUSTED: quota exceeded")
    assert _is_retryable_availability_error(quota_exc, has_fallback=True) is True
    assert _is_retryable_availability_error(quota_exc, has_fallback=False) is False


def test_server_error_always_retryable():
    """500/503 server errors are retryable regardless of fallback model."""
    from noir.infrastructure.ai.gemini import _is_retryable_availability_error

    server_exc = Exception("503 UNAVAILABLE: server overloaded")
    assert _is_retryable_availability_error(server_exc, has_fallback=True) is True
    assert _is_retryable_availability_error(server_exc, has_fallback=False) is True


def test_timeout_always_retryable():
    """Timeouts are retryable regardless of fallback."""
    from noir.infrastructure.ai.gemini import _is_retryable_availability_error

    assert _is_retryable_availability_error(TimeoutError(), has_fallback=False) is True
    assert _is_retryable_availability_error(ConnectionError(), has_fallback=False) is True


# ── Shared utility tests ─────────────────────────────────────────────


def test_is_label_task_detects_rename():
    """Module-level is_label_task should detect label-related requests."""
    from noir.infrastructure.ai.discovery import is_label_task

    assert is_label_task("Rename app label to MyApp") is True
    assert is_label_task("change the display name") is True
    assert (
        is_label_task(
            'change the name and flash a message saying "ola it works" whenever I interact'
        )
        is False
    )
    assert is_label_task("give unlimited coins") is False
    assert is_label_task("add network permission") is False


def test_has_exact_evidence_with_file_read():
    """Module-level has_exact_evidence should detect file reads."""
    from noir.infrastructure.ai.discovery import DiscoveryResult, has_exact_evidence

    result = DiscoveryResult(seen_files={"smali/com/game/Score.smali": ".class X"})
    assert has_exact_evidence(result, "change the score") is True


def test_has_exact_evidence_empty_result():
    """Empty discovery result has no exact evidence."""
    from noir.infrastructure.ai.discovery import DiscoveryResult, has_exact_evidence

    result = DiscoveryResult()
    assert has_exact_evidence(result, "change something") is False


# ── DiscoveryToolExecutor tests ──────────────────────────────────────


def test_tool_executor_standalone_search(ws):
    """DiscoveryToolExecutor works independently of any provider."""
    from noir.infrastructure.ai.discovery import DiscoveryResult, DiscoveryToolExecutor

    target = ws.decoded_dir / "smali" / "Executor.smali"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("executor test content")

    analysis = _make_analysis(ws, runtimes={"dalvik"})
    executor = DiscoveryToolExecutor(AiContextTools(ws, analysis))
    result = DiscoveryResult()

    output, summary, nbytes = executor.execute("search_workspace", {"query": "executor"}, result)
    assert nbytes > 0


def test_tool_executor_standalone_list(ws):
    """DiscoveryToolExecutor list_directory works independently."""
    from noir.infrastructure.ai.discovery import DiscoveryResult, DiscoveryToolExecutor

    target = ws.decoded_dir / "res" / "values" / "strings.xml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("<resources/>")

    analysis = _make_analysis(ws, runtimes={"dalvik"})
    executor = DiscoveryToolExecutor(AiContextTools(ws, analysis))
    result = DiscoveryResult()

    output, summary, nbytes = executor.execute("list_directory", {"subdir": "res"}, result)
    assert "strings.xml" in output


# ── Local discovery provider tests ───────────────────────────────────


def test_local_discovery_provider_returns_static():
    """LocalDiscoveryProvider always returns static fallback."""
    from noir.infrastructure.ai.local_discovery import LocalDiscoveryProvider

    config = NoirConfig(
        _env_file=None,
        data_dir=str(Path.cwd()),
        gemini_api_key="test",
    )
    provider = LocalDiscoveryProvider(config)
    result = provider.discover("anything", MagicMock(), MagicMock())

    assert result.used_static_fallback is True
    assert result.stop_reason == "local_only"
    assert result.api_calls == 0
    assert result.transcript == []


# ── Discovery provider factory tests ─────────────────────────────────


def test_factory_creates_local_provider():
    """Factory with discovery_provider='local' creates LocalDiscoveryProvider."""
    from noir.application.ai_service import _create_discovery_provider
    from noir.infrastructure.ai.local_discovery import LocalDiscoveryProvider

    config = NoirConfig(
        _env_file=None,
        data_dir=str(Path.cwd()),
        gemini_api_key="test",
        discovery_provider="local",
    )
    provider = _create_discovery_provider(config)
    assert isinstance(provider, LocalDiscoveryProvider)


def test_factory_returns_none_for_gemini():
    """Factory with discovery_provider='gemini' returns None (legacy path)."""
    from noir.application.ai_service import _create_discovery_provider

    config = NoirConfig(
        _env_file=None,
        data_dir=str(Path.cwd()),
        gemini_api_key="test",
        discovery_provider="gemini",
    )
    provider = _create_discovery_provider(config)
    assert provider is None


def test_factory_openrouter_fallback_on_missing_key():
    """Factory falls back to local when OpenRouter key is missing."""
    from noir.application.ai_service import _create_discovery_provider
    from noir.infrastructure.ai.local_discovery import LocalDiscoveryProvider

    config = NoirConfig(
        _env_file=None,
        data_dir=str(Path.cwd()),
        gemini_api_key="test",
        openrouter_api_key="",
        discovery_provider="openrouter",
    )
    provider = _create_discovery_provider(config)
    assert isinstance(provider, LocalDiscoveryProvider)


# ── Stall timeout tests ─────────────────────────────────────────────


def test_stall_timeout_raises_after_deadline():
    """Gemini _call_model should raise when stall timeout is exceeded."""

    from noir.infrastructure.ai.gemini import GeminiProvider, GeminiProviderError

    config = NoirConfig(
        _env_file=None,
        gemini_api_key="unit-test-only",
        ai_provider="gemini",
        ai_model="gemini-test",
        ai_stall_timeout=0,  # Immediate timeout
    )
    provider = GeminiProvider(config=config)

    # The method should fail at the stall check before even calling the SDK
    with pytest.raises(GeminiProviderError, match="stall timeout"):
        provider._call_model('{"test": true}')
