"""Automated unit tests for deterministic intent-based file routing subsystem."""

import logging
import re

import pytest

from noir.domain.config import NoirConfig
from noir.domain.models import AnalysisResult, ComponentInfo, SmaliClassInfo
from noir.infrastructure.ai.context import AiContextTools
from noir.infrastructure.ai.intent_router import (
    IntentRouter,
    IntentRule,
    detect_runtimes,
)
from noir.infrastructure.filesystem.workspace import ProjectWorkspace


@pytest.fixture
def workspace(tmp_path):
    from noir.domain.models import ProjectInfo
    from noir.infrastructure.database.engine import init_db
    from noir.infrastructure.database.repositories import ProjectRepository

    config = NoirConfig(_env_file=None, data_dir=str(tmp_path), gemini_api_key="test-key")
    init_db(config.effective_database_url)
    ProjectRepository().create(
        ProjectInfo(id="test_router", name="Test", package_name="com.example")
    )
    ws = ProjectWorkspace("test_router", config)
    ws.create()
    return ws


@pytest.fixture
def sample_dex_workspace(workspace):
    """Workspace populated with standard DEX / Android files."""
    (workspace.decoded_dir / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '  <application android:label="@string/app_name">\n'
        '    <activity android:name="com.example.MainActivity">\n'
        "      <intent-filter>\n"
        '        <action android:name="android.intent.action.MAIN"/>\n'
        '        <category android:name="android.intent.category.LAUNCHER"/>\n'
        "      </intent-filter>\n"
        "    </activity>\n"
        '    <receiver android:name="com.example.BootReceiver"/>\n'
        '    <service android:name="com.example.SyncService"/>\n'
        "  </application>\n"
        "</manifest>"
    )
    values_dir = workspace.decoded_dir / "res/values"
    values_dir.mkdir(parents=True)
    (values_dir / "strings.xml").write_text(
        '<resources><string name="app_name">My App</string></resources>'
    )
    values_en = workspace.decoded_dir / "res/values-en"
    values_en.mkdir(parents=True)
    (values_en / "strings.xml").write_text(
        '<resources><string name="app_name">My App EN</string></resources>'
    )
    layout_dir = workspace.decoded_dir / "res/layout"
    layout_dir.mkdir(parents=True)
    (layout_dir / "activity_main.xml").write_text("<LinearLayout/>")
    (layout_dir / "item_view.xml").write_text("<TextView/>")

    smali_dir = workspace.decoded_dir / "smali/com/example"
    smali_dir.mkdir(parents=True)
    (smali_dir / "MainActivity.smali").write_text(".class public Lcom/example/MainActivity;\n")
    (smali_dir / "BootReceiver.smali").write_text(".class public Lcom/example/BootReceiver;\n")
    (smali_dir / "SyncService.smali").write_text(".class public Lcom/example/SyncService;\n")
    (smali_dir / "HttpClient.smali").write_text(".class public Lcom/example/HttpClient;\n")

    return workspace


@pytest.fixture
def sample_analysis():
    return AnalysisResult(
        project_id="test_router",
        package_name="com.example",
        runtimes={"dalvik"},
        runtime="dalvik",
        components=[
            ComponentInfo(
                name="com.example.MainActivity",
                component_type="activity",
                is_launcher=True,
            ),
            ComponentInfo(
                name="com.example.BootReceiver",
                component_type="receiver",
            ),
            ComponentInfo(
                name="com.example.SyncService",
                component_type="service",
            ),
        ],
        smali_classes=[
            SmaliClassInfo(
                descriptor="Lcom/example/MainActivity;",
                file_path="smali/com/example/MainActivity.smali",
            ),
            SmaliClassInfo(
                descriptor="Lcom/example/BootReceiver;",
                file_path="smali/com/example/BootReceiver.smali",
            ),
            SmaliClassInfo(
                descriptor="Lcom/example/SyncService;",
                file_path="smali/com/example/SyncService.smali",
            ),
            SmaliClassInfo(
                descriptor="Lcom/example/HttpClient;",
                file_path="smali/com/example/HttpClient.smali",
            ),
        ],
    )


# ── R1 / R4: Positive trigger tests for all 12 intents ────────────────


@pytest.mark.parametrize(
    ("intent_id", "prompt", "extra_runtimes"),
    [
        ("app_name", "rename the app to CoolApp", set()),
        ("app_name", "change app name to NewName", set()),
        ("app_name", "update display name", set()),
        ("app_name", "change the label of the application", set()),
        ("toast_flash", "show a toast on click", {"dalvik"}),
        ("toast_flash", "flash message when user taps button", {"dalvik"}),
        ("toast_flash", "popup alert every time user interacts", {"dalvik"}),
        ("network_ping", "ping endpoint on launch", {"dalvik"}),
        ("network_ping", "send http request to server on startup", {"dalvik"}),
        ("network_ping", "post data to url on start", {"dalvik"}),
        ("network_ping", "call https://api.example.com/ping on startup", {"dalvik"}),
        ("ui_layout", "change button text color", {"dalvik"}),
        ("ui_layout", "modify screen layout", {"dalvik"}),
        ("ui_layout", "change background color of view", {"dalvik"}),
        ("ui_layout", "update layout xml", {"dalvik"}),
        ("permission", "grant camera permission", set()),
        ("permission", "revoke internet permissions", set()),
        ("permission", "add permission android.permission.CAMERA", set()),
        ("receiver_service", "add broadcast receiver for boot", {"dalvik"}),
        ("receiver_service", "modify background service", {"dalvik"}),
        ("receiver_service", "register receiver on launch", {"dalvik"}),
        ("react_native_js", "modify react native js bundle", {"react_native"}),
        ("react_native_js", "update hermes bytecode in index.android.bundle", {"hermes"}),
        ("flutter_dart", "modify flutter dart code", {"flutter"}),
        ("flutter_dart", "patch libapp.so in flutter app", {"flutter"}),
        ("unity_mono", "modify unity c# script", {"mono"}),
        ("unity_mono", "update Assembly-CSharp.dll", {"mono"}),
        ("unity_mono", "modify managed dll in assets", {"mono"}),
        ("unity_il2cpp", "patch libil2cpp.so binary", {"il2cpp"}),
        ("unity_il2cpp", "modify global-metadata.dat", {"il2cpp"}),
        ("native_elf", "patch native .so library", {"native"}),
        ("native_elf", "modify jni function implementation", {"native"}),
        ("xamarin_dotnet", "modify xamarin .net assemblies", {"xamarin"}),
        ("xamarin_dotnet", "patch dotnet assemblies in app", {"xamarin"}),
    ],
)
def test_positive_trigger_patterns_for_all_12_intents(
    workspace, sample_analysis, intent_id, prompt, extra_runtimes
):
    analysis = AnalysisResult(
        project_id=sample_analysis.project_id,
        runtimes={"dalvik", *extra_runtimes},
        runtime="dalvik",
    )
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()
    result = router.route(prompt, tools, analysis)

    assert intent_id in result.matched_intents
    assert result.stop_reason == "intent_matched"


# ── Negative trigger tests for all 12 intents ─────────────────────────


@pytest.mark.parametrize(
    ("intent_id", "prompt"),
    [
        ("app_name", "clean up unused database tables"),
        ("toast_flash", "clean up unused database tables"),
        ("network_ping", "optimize image compression ratio"),
        ("ui_layout", "clean up unused database tables"),
        ("permission", "format source code with linter"),
        ("receiver_service", "optimize battery consumption"),
        ("react_native_js", "clean up unused database tables"),
        ("flutter_dart", "clean up unused database tables"),
        ("unity_mono", "clean up unused database tables"),
        ("unity_il2cpp", "clean up unused database tables"),
        ("native_elf", "clean up unused database tables"),
        ("xamarin_dotnet", "clean up unused database tables"),
    ],
)
def test_negative_trigger_patterns_for_all_12_intents(
    workspace, sample_analysis, intent_id, prompt
):
    all_runtimes = {
        "dalvik",
        "flutter",
        "react_native",
        "hermes",
        "mono",
        "il2cpp",
        "native",
        "xamarin",
    }
    analysis = AnalysisResult(
        project_id=sample_analysis.project_id,
        runtimes=all_runtimes,
    )
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()
    result = router.route(prompt, tools, analysis)

    assert intent_id not in result.matched_intents


# ── R1: File candidate resolution tests per intent ────────────────────


def test_app_name_file_resolution(sample_dex_workspace, sample_analysis):
    tools = AiContextTools(sample_dex_workspace, sample_analysis)
    result = IntentRouter().route("change app name to Hello", tools, sample_analysis)
    assert "app_name" in result.matched_intents
    assert "AndroidManifest.xml" in result.seen_files
    assert "res/values/strings.xml" in result.seen_files
    assert "res/values-en/strings.xml" in result.seen_files


def test_toast_flash_file_resolution(sample_dex_workspace, sample_analysis):
    tools = AiContextTools(sample_dex_workspace, sample_analysis)
    result = IntentRouter().route("show toast on button click", tools, sample_analysis)
    assert "toast_flash" in result.matched_intents
    assert "smali/com/example/MainActivity.smali" in result.seen_files


def test_network_ping_file_resolution(sample_dex_workspace, sample_analysis):
    tools = AiContextTools(sample_dex_workspace, sample_analysis)
    result = IntentRouter().route("ping server on launch", tools, sample_analysis)
    assert "network_ping" in result.matched_intents
    assert "AndroidManifest.xml" in result.seen_files
    assert "smali/com/example/MainActivity.smali" in result.seen_files
    assert "smali/com/example/HttpClient.smali" in result.seen_files


def test_ui_layout_file_resolution(sample_dex_workspace, sample_analysis):
    tools = AiContextTools(sample_dex_workspace, sample_analysis)
    result = IntentRouter().route("change button text in layout", tools, sample_analysis)
    assert "ui_layout" in result.matched_intents
    assert "res/layout/activity_main.xml" in result.seen_files
    assert "res/layout/item_view.xml" in result.seen_files
    assert "res/values/strings.xml" in result.seen_files


def test_permission_file_resolution(sample_dex_workspace, sample_analysis):
    tools = AiContextTools(sample_dex_workspace, sample_analysis)
    result = IntentRouter().route("grant camera permission", tools, sample_analysis)
    assert "permission" in result.matched_intents
    assert "AndroidManifest.xml" in result.seen_files


def test_receiver_service_file_resolution(sample_dex_workspace, sample_analysis):
    tools = AiContextTools(sample_dex_workspace, sample_analysis)
    result = IntentRouter().route("modify background service", tools, sample_analysis)
    assert "receiver_service" in result.matched_intents
    assert "AndroidManifest.xml" in result.seen_files
    assert "smali/com/example/SyncService.smali" in result.seen_files


def test_react_native_file_resolution(workspace):
    (workspace.decoded_dir / "assets").mkdir(parents=True)
    (workspace.decoded_dir / "assets/index.android.bundle").write_text("var x = 1;")
    (workspace.decoded_dir / "lib/arm64-v8a").mkdir(parents=True)
    (workspace.decoded_dir / "lib/arm64-v8a/libhermes.so").write_bytes(b"ELF_HERMES")
    (workspace.decoded_dir / "AndroidManifest.xml").write_text("<manifest/>")

    analysis = AnalysisResult(project_id="test", runtimes={"react_native", "hermes"})
    tools = AiContextTools(workspace, analysis)
    result = IntentRouter().route("update react native js bundle", tools, analysis)

    assert "react_native_js" in result.matched_intents
    assert "assets/index.android.bundle" in result.seen_files
    assert "lib/arm64-v8a/libhermes.so" in result.seen_files
    assert "AndroidManifest.xml" in result.seen_files


def test_flutter_dart_file_resolution(workspace):
    (workspace.decoded_dir / "lib/arm64-v8a").mkdir(parents=True)
    (workspace.decoded_dir / "lib/arm64-v8a/libapp.so").write_bytes(b"ELF_LIBAPP")
    (workspace.decoded_dir / "lib/arm64-v8a/libflutter.so").write_bytes(b"ELF_LIBFLUTTER")
    (workspace.decoded_dir / "assets/flutter_assets").mkdir(parents=True)
    (workspace.decoded_dir / "assets/flutter_assets/kernel_blob.bin").write_bytes(b"DART_KERNEL")

    analysis = AnalysisResult(project_id="test", runtimes={"flutter"})
    tools = AiContextTools(workspace, analysis)
    result = IntentRouter().route("modify flutter dart code", tools, analysis)

    assert "flutter_dart" in result.matched_intents
    assert "lib/arm64-v8a/libapp.so" in result.seen_files
    assert "lib/arm64-v8a/libflutter.so" in result.seen_files
    assert "assets/flutter_assets/kernel_blob.bin" in result.seen_files


def test_unity_mono_file_resolution(workspace):
    managed = workspace.decoded_dir / "assets/bin/Data/Managed"
    managed.mkdir(parents=True)
    (managed / "Assembly-CSharp.dll").write_bytes(b"MZ_DLL")
    (managed / "UnityEngine.dll").write_bytes(b"MZ_DLL2")
    (workspace.decoded_dir / "lib/arm64-v8a").mkdir(parents=True)
    (workspace.decoded_dir / "lib/arm64-v8a/libmono.so").write_bytes(b"ELF_MONO")

    analysis = AnalysisResult(project_id="test", runtimes={"mono"})
    tools = AiContextTools(workspace, analysis)
    result = IntentRouter().route("modify unity c# script", tools, analysis)

    assert "unity_mono" in result.matched_intents
    assert "assets/bin/Data/Managed/Assembly-CSharp.dll" in result.seen_files
    assert "assets/bin/Data/Managed/UnityEngine.dll" in result.seen_files
    assert "lib/arm64-v8a/libmono.so" in result.seen_files


def test_unity_il2cpp_file_resolution(workspace):
    (workspace.decoded_dir / "lib/arm64-v8a").mkdir(parents=True)
    (workspace.decoded_dir / "lib/arm64-v8a/libil2cpp.so").write_bytes(b"ELF_IL2CPP")
    (workspace.decoded_dir / "assets/bin/Data/Managed/etc/metadata").mkdir(parents=True)
    (
        workspace.decoded_dir / "assets/bin/Data/Managed/etc/metadata/global-metadata.dat"
    ).write_bytes(b"DAT")

    analysis = AnalysisResult(project_id="test", runtimes={"il2cpp"})
    tools = AiContextTools(workspace, analysis)
    result = IntentRouter().route("patch libil2cpp.so binary", tools, analysis)

    assert "unity_il2cpp" in result.matched_intents
    assert "lib/arm64-v8a/libil2cpp.so" in result.seen_files
    assert "assets/bin/Data/Managed/etc/metadata/global-metadata.dat" in result.seen_files


def test_native_elf_file_resolution(workspace):
    (workspace.decoded_dir / "lib/arm64-v8a").mkdir(parents=True)
    (workspace.decoded_dir / "lib/arm64-v8a/libsecurity.so").write_bytes(b"ELF")
    (workspace.decoded_dir / "lib/armeabi-v7a").mkdir(parents=True)
    (workspace.decoded_dir / "lib/armeabi-v7a/libsecurity.so").write_bytes(b"ELF32")

    analysis = AnalysisResult(project_id="test", runtimes={"native"})
    tools = AiContextTools(workspace, analysis)
    result = IntentRouter().route("patch native .so library", tools, analysis)

    assert "native_elf" in result.matched_intents
    assert "lib/arm64-v8a/libsecurity.so" in result.seen_files
    assert "lib/armeabi-v7a/libsecurity.so" in result.seen_files


def test_xamarin_dotnet_file_resolution(workspace):
    assemblies = workspace.decoded_dir / "assemblies"
    assemblies.mkdir(parents=True)
    (assemblies / "App.dll").write_bytes(b"MZ_DLL")
    (assemblies / "Xamarin.Forms.dll").write_bytes(b"MZ_DLL2")

    analysis = AnalysisResult(project_id="test", runtimes={"xamarin"})
    tools = AiContextTools(workspace, analysis)
    result = IntentRouter().route("modify xamarin .net assemblies", tools, analysis)

    assert "xamarin_dotnet" in result.matched_intents
    assert "assemblies/App.dll" in result.seen_files
    assert "assemblies/Xamarin.Forms.dll" in result.seen_files


# ── R1 / R4: Runtime scoping & marker scanning tests ──────────────────


def test_flutter_intent_skipped_when_runtime_markers_absent(sample_dex_workspace, sample_analysis):
    """Flutter intent should NOT fire for plain DEX app without Flutter markers/runtime."""
    tools = AiContextTools(sample_dex_workspace, sample_analysis)
    result = IntentRouter().route("modify flutter dart code", tools, sample_analysis)
    assert "flutter_dart" not in result.matched_intents
    assert result.stop_reason == "no_intent_matched"


def test_unity_intent_skipped_when_runtime_markers_absent(sample_dex_workspace, sample_analysis):
    """Unity Mono and IL2CPP intents should NOT fire for plain DEX app."""
    tools = AiContextTools(sample_dex_workspace, sample_analysis)
    result = IntentRouter().route("modify unity c# script and il2cpp", tools, sample_analysis)
    assert "unity_mono" not in result.matched_intents
    assert "unity_il2cpp" not in result.matched_intents


def test_react_native_skipped_when_runtime_markers_absent(sample_dex_workspace, sample_analysis):
    """React Native intent should NOT fire for plain DEX app."""
    tools = AiContextTools(sample_dex_workspace, sample_analysis)
    result = IntentRouter().route("modify react native js bundle", tools, sample_analysis)
    assert "react_native_js" not in result.matched_intents


def test_runtime_detection_falls_back_to_marker_scanning(workspace):
    """When analysis.runtimes is empty or None, router scans directory markers."""
    (workspace.decoded_dir / "lib/arm64-v8a").mkdir(parents=True)
    (workspace.decoded_dir / "lib/arm64-v8a/libflutter.so").write_bytes(b"ELF")

    tools = AiContextTools(workspace)
    runtimes = detect_runtimes(tools, analysis=None)
    assert "flutter" in runtimes
    assert "native" in runtimes

    result = IntentRouter().route("modify flutter code", tools, analysis=None)
    assert "flutter_dart" in result.matched_intents
    assert "lib/arm64-v8a/libflutter.so" in result.seen_files


def test_runtime_detection_prefers_analysis_metadata(workspace):
    """When analysis.runtimes is specified, it takes precedence."""
    (workspace.decoded_dir / "lib/arm64-v8a").mkdir(parents=True)
    (workspace.decoded_dir / "lib/arm64-v8a/libflutter.so").write_bytes(b"ELF")

    analysis = AnalysisResult(project_id="test", runtimes={"dalvik"})
    tools = AiContextTools(workspace, analysis)

    runtimes = detect_runtimes(tools, analysis=analysis)
    assert runtimes == {"dalvik"}
    result = IntentRouter().route("modify flutter code", tools, analysis)
    assert "flutter_dart" not in result.matched_intents


# ── R1 / R4: Union matching for composite requests ───────────────────


def test_union_matching_combines_intents_without_duplicates(sample_dex_workspace, sample_analysis):
    """A request matching multiple intents should combine candidate files and deduplicate."""
    tools = AiContextTools(sample_dex_workspace, sample_analysis)
    result = IntentRouter().route("rename app and show toast on tap", tools, sample_analysis)

    assert "app_name" in result.matched_intents
    assert "toast_flash" in result.matched_intents
    assert result.stop_reason == "intent_matched"

    assert "AndroidManifest.xml" in result.seen_files
    assert "res/values/strings.xml" in result.seen_files
    assert "smali/com/example/MainActivity.smali" in result.seen_files
    assert len(result.seen_files) <= 20


def test_composite_three_intents_merging(sample_dex_workspace, sample_analysis):
    """A request matching app_name, toast_flash, and permission."""
    tools = AiContextTools(sample_dex_workspace, sample_analysis)
    result = IntentRouter().route(
        "rename app, grant camera permission, and show toast on tap",
        tools,
        sample_analysis,
    )

    assert set(result.matched_intents) == {"app_name", "toast_flash", "permission"}
    assert "AndroidManifest.xml" in result.seen_files
    assert "res/values/strings.xml" in result.seen_files
    assert "smali/com/example/MainActivity.smali" in result.seen_files


# ── R1 / R4: File count capping ──────────────────────────────────────


def test_per_intent_file_cap_enforced(workspace):
    """Each intent must not contribute more than 8 files."""
    layout_dir = workspace.decoded_dir / "res/layout"
    layout_dir.mkdir(parents=True)
    for i in range(25):
        (layout_dir / f"view_{i:02d}.xml").write_text(f"<View id='{i}'/>")

    analysis = AnalysisResult(project_id="test", runtimes={"dalvik"})
    tools = AiContextTools(workspace, analysis)

    result = IntentRouter().route("modify screen layout", tools, analysis)
    assert "ui_layout" in result.matched_intents
    assert len(result.seen_files) == 8


def test_total_file_cap_enforced_at_20(workspace):
    """Total files across all matched intents must never exceed 20."""
    rule1 = IntentRule(
        intent_id="intent_1",
        trigger_patterns=[re.compile(r"intent_one")],
        apk_types=set(),
        file_selector=lambda t, a: [f"file_1_{i}.txt" for i in range(10)],
    )
    rule2 = IntentRule(
        intent_id="intent_2",
        trigger_patterns=[re.compile(r"intent_two")],
        apk_types=set(),
        file_selector=lambda t, a: [f"file_2_{i}.txt" for i in range(10)],
    )
    rule3 = IntentRule(
        intent_id="intent_3",
        trigger_patterns=[re.compile(r"intent_three")],
        apk_types=set(),
        file_selector=lambda t, a: [f"file_3_{i}.txt" for i in range(10)],
    )

    router = IntentRouter(rules=[rule1, rule2, rule3])
    tools = AiContextTools(workspace)

    result = router.route("intent_one intent_two intent_three", tools)

    assert result.matched_intents == ["intent_1", "intent_2", "intent_3"]
    assert len(result.seen_files) == 20


# ── R2 / R4: Fallback behavior & warning log verification ────────────


def test_fallback_behavior_and_warning_logged(sample_dex_workspace, sample_analysis, caplog):
    """Unknown requests return no_intent_matched and log warning."""
    tools = AiContextTools(sample_dex_workspace, sample_analysis)
    router = IntentRouter()

    with caplog.at_level(logging.WARNING):
        result = router.route(
            "clean up unused database tables and refactor", tools, sample_analysis
        )

    assert result.matched_intents == []
    assert result.seen_files == {}
    assert result.stop_reason == "no_intent_matched"

    assert any(
        "No intent matched for request; falling through to AI discovery" in record.message
        for record in caplog.records
    )


# ── R2: AI Service Integration in generate_plan ───────────────────────


def test_ai_service_generate_plan_bypasses_discovery_on_match(
    sample_dex_workspace, sample_analysis, monkeypatch
):
    """When an intent matches, generate_plan bypasses AI discovery (0 API calls)."""
    from noir.application.ai_service import generate_plan
    from noir.domain.models import ChangePlan, PlanFileChange

    cfg = sample_dex_workspace.config
    # keep cfg.data_dir as is

    class MockProject:
        workspace_revision = 0

    monkeypatch.setattr(
        "noir.application.ai_service.ProjectRepository.get",
        lambda self, pid: MockProject(),
    )
    monkeypatch.setattr(
        "noir.application.ai_service.AnalysisService.analyze",
        lambda *args, **kwargs: sample_analysis,
    )
    monkeypatch.setattr(
        "noir.application.ai_service.require_clean_workspace",
        lambda *args, **kwargs: None,
    )

    class MockGenProvider:
        def __init__(self, **kwargs):
            pass

        def generate_plan(self, request, analysis, context, *, project_id):
            assert context.get("detected_intents") == ["app_name"]
            assert "AndroidManifest.xml" in context.get("file_snippets", {})
            return ChangePlan(
                project_id=project_id,
                workspace_revision=0,
                user_request=request,
                file_changes=[
                    PlanFileChange(
                        relative_path="AndroidManifest.xml",
                        operation="manifest_update",
                    )
                ],
                intended_outcome="Rename app",
            )

    monkeypatch.setattr("noir.application.ai_service.GeminiProvider", MockGenProvider)
    monkeypatch.setattr("noir.application.ai_service._create_discovery_provider", lambda c: None)

    plan = generate_plan(cfg, sample_dex_workspace.project_id, "rename app to BestApp", True)

    assert plan.discovery_api_calls == 0
    assert plan.discovery_stop_reason == "hybrid_deterministic_router"
    assert plan.discovery_transcript == []


def test_ai_service_generate_plan_falls_through_on_no_match(
    sample_dex_workspace, sample_analysis, monkeypatch, caplog
):
    """When no intent matches, generate_plan logs warning and runs normal discovery."""
    from noir.application.ai_service import generate_plan
    from noir.domain.models import ChangePlan
    from noir.infrastructure.ai.discovery import DiscoveryResult

    cfg = sample_dex_workspace.config
    # keep cfg.data_dir as is

    class MockProject:
        workspace_revision = 0

    monkeypatch.setattr(
        "noir.application.ai_service.ProjectRepository.get",
        lambda self, pid: MockProject(),
    )
    monkeypatch.setattr(
        "noir.application.ai_service.AnalysisService.analyze",
        lambda *args, **kwargs: sample_analysis,
    )
    monkeypatch.setattr(
        "noir.application.ai_service.require_clean_workspace",
        lambda *args, **kwargs: None,
    )

    class MockDiscoveryProvider:
        def discover(self, request, context_tools, analysis):
            return DiscoveryResult(
                seen_files={"sample.txt": "content"},
                api_calls=2,
                stop_reason="discovery_complete",
            )

    monkeypatch.setattr(
        "noir.application.ai_service._create_discovery_provider",
        lambda c: MockDiscoveryProvider(),
    )

    class MockGenProvider:
        def __init__(self, **kwargs):
            pass

        def generate_plan(self, request, analysis, context, *, project_id):
            return ChangePlan(
                project_id=project_id,
                workspace_revision=0,
                user_request=request,
                file_changes=[],
                intended_outcome="Refactor",
            )

    monkeypatch.setattr("noir.application.ai_service.GeminiProvider", MockGenProvider)

    with caplog.at_level(logging.WARNING):
        plan = generate_plan(
            cfg, sample_dex_workspace.project_id, "refactor everything completely", True
        )

    assert plan.discovery_api_calls == 2
    assert plan.discovery_stop_reason == "discovery_complete"
    assert any(
        "No intent matched for request; falling through to AI discovery" in record.message
        for record in caplog.records
    )
