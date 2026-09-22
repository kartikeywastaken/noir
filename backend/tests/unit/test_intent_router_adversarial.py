"""Adversarial unit tests for deterministic intent-based file routing subsystem."""

import pytest

from noir.domain.config import NoirConfig
from noir.domain.models import AnalysisResult, ComponentInfo
from noir.infrastructure.ai.context import AiContextTools
from noir.infrastructure.ai.intent_router import (
    IntentRouter,
    scan_directory_markers,
)
from noir.infrastructure.filesystem.workspace import ProjectWorkspace


@pytest.fixture
def workspace(tmp_path):
    from noir.domain.models import ProjectInfo
    from noir.infrastructure.database.engine import init_db
    from noir.infrastructure.database.repositories import ProjectRepository

    config = NoirConfig(_env_file=None, data_dir=str(tmp_path), gemini_api_key="test-key")
    init_db(config.effective_database_url)
    ProjectRepository().create(ProjectInfo(id="test_adv", name="Test", package_name="com.example"))
    ws = ProjectWorkspace("test_adv", config)
    ws.create()
    return ws


def test_adversarial_regex_boundaries(workspace):
    """Test boundary punctuation handling: c#, c++, .net, and text in ui_layout."""
    analysis = AnalysisResult(
        project_id="test",
        runtimes={"mono", "native", "xamarin", "dalvik"},
    )
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()

    # 1. c# alone (without the word unity)
    res = router.route("modify c# code in project", tools, analysis)
    assert "unity_mono" in res.matched_intents

    # 2. c++ alone
    res = router.route("patch c++ function implementation", tools, analysis)
    assert "native_elf" in res.matched_intents

    # 3. .so alone
    res = router.route("update .so library in lib", tools, analysis)
    assert "native_elf" in res.matched_intents

    # 4. .net alone (without the word xamarin)
    res = router.route("modify .net assemblies in application", tools, analysis)
    assert "xamarin_dotnet" in res.matched_intents

    # 5. ui_layout "modify text"
    res = router.route("modify text in the main layout", tools, analysis)
    assert "ui_layout" in res.matched_intents

    # 6. app_name "update application name"
    res = router.route("update application name to SuperApp", tools, analysis)
    assert "app_name" in res.matched_intents


def test_primary_smali_prioritized_over_inner_classes(workspace):
    """When an Activity has many inner classes ($1, $2, etc.), MainActivity.smali must NOT be capped out."""
    smali_dir = workspace.decoded_dir / "smali/com/example"
    smali_dir.mkdir(parents=True)
    # Create 12 inner anonymous classes which would sort before MainActivity.smali alphabetically
    for i in range(1, 13):
        (smali_dir / f"MainActivity${i}.smali").write_text(
            f".class public Lcom/example/MainActivity${i};"
        )
    (smali_dir / "MainActivity.smali").write_text(".class public Lcom/example/MainActivity;")

    analysis = AnalysisResult(
        project_id="test",
        runtimes={"dalvik"},
        components=[
            ComponentInfo(
                name="com.example.MainActivity",
                component_type="activity",
                is_launcher=True,
            )
        ],
    )
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()

    result = router.route("show toast on button click", tools, analysis)
    assert "toast_flash" in result.matched_intents
    # MainActivity.smali MUST be present in seen_files despite the 8-file cap
    assert "smali/com/example/MainActivity.smali" in result.seen_files
    assert len(result.seen_files) <= 8


def test_hybrid_react_native_and_native_elf_deduplication(workspace):
    """Hybrid APK with React Native and custom Native libraries triggers both and deduplicates .so files."""
    (workspace.decoded_dir / "assets").mkdir(parents=True)
    (workspace.decoded_dir / "assets/index.android.bundle").write_text("console.log('RN');")
    (workspace.decoded_dir / "lib/arm64-v8a").mkdir(parents=True)
    (workspace.decoded_dir / "lib/arm64-v8a/libhermes.so").write_bytes(b"ELF_HERMES")
    (workspace.decoded_dir / "lib/arm64-v8a/libcustom_jni.so").write_bytes(b"ELF_CUSTOM")
    (workspace.decoded_dir / "AndroidManifest.xml").write_text("<manifest/>")

    analysis = AnalysisResult(
        project_id="test",
        runtimes={"react_native", "native"},
    )
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()

    result = router.route(
        "update react native bundle and patch native jni library", tools, analysis
    )

    assert "react_native_js" in result.matched_intents
    assert "native_elf" in result.matched_intents
    assert "assets/index.android.bundle" in result.seen_files
    assert "lib/arm64-v8a/libhermes.so" in result.seen_files
    assert "lib/arm64-v8a/libcustom_jni.so" in result.seen_files
    # Verify no duplicates in seen_files keys
    assert len(result.seen_files) == len(set(result.seen_files.keys()))


def test_unconventional_assembly_path_detection(workspace):
    """Assemblies in custom directories (e.g. custom/assemblies/) are detected by marker scan and selector."""
    custom_dir = workspace.decoded_dir / "custom/assemblies"
    custom_dir.mkdir(parents=True)
    (custom_dir / "Mono.Android.dll").write_bytes(b"MZ_DLL")

    tools = AiContextTools(workspace)
    runtimes = scan_directory_markers(tools)
    assert "xamarin" in runtimes

    result = IntentRouter().route("modify xamarin assemblies", tools)
    assert "xamarin_dotnet" in result.matched_intents
    assert "custom/assemblies/Mono.Android.dll" in result.seen_files


def test_possessive_app_name_regex(workspace):
    """Test possessive phrasing for app_name: app's and application's."""
    analysis = AnalysisResult(project_id="test", runtimes={"dalvik"})
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()

    # Possessive app's name
    res = router.route("change the app's name to SuperApp", tools, analysis)
    assert "app_name" in res.matched_intents

    # Possessive application's title
    res = router.route("update application's title to SuperApp", tools, analysis)
    assert "app_name" in res.matched_intents

    # Modify app's label
    res = router.route("modify the app's label", tools, analysis)
    assert "app_name" in res.matched_intents

    # Set app name
    res = router.route("set app name to NewName", tools, analysis)
    assert "app_name" in res.matched_intents


def test_network_ping_verbs(workspace):
    """Test connect, fetch, and get verbs for network_ping."""
    analysis = AnalysisResult(project_id="test", runtimes={"dalvik"})
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()

    # connect to server on launch
    res = router.route("connect to server on launch", tools, analysis)
    assert "network_ping" in res.matched_intents

    # fetch data from api on app start
    res = router.route("fetch data from api on app start", tools, analysis)
    assert "network_ping" in res.matched_intents

    # connect to backend on launch
    res = router.route("connect to backend on launch", tools, analysis)
    assert "network_ping" in res.matched_intents


def test_ui_layout_and_receiver_deletion_verbs(workspace):
    """Test remove, delete, and unregister verbs for ui_layout and receiver_service."""
    analysis = AnalysisResult(project_id="test", runtimes={"dalvik"})
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()

    # remove button from layout
    res = router.route("remove button from screen layout", tools, analysis)
    assert "ui_layout" in res.matched_intents

    # delete text view
    res = router.route("delete text view from screen", tools, analysis)
    assert "ui_layout" in res.matched_intents

    # unregister receiver
    res = router.route("unregister receiver on app pause", tools, analysis)
    assert "receiver_service" in res.matched_intents

    # remove background service
    res = router.route("remove background service", tools, analysis)
    assert "receiver_service" in res.matched_intents


def test_xamarin_mono_android_dll_regex(workspace):
    """Test Mono.Android.dll and singular assembly phrasing for xamarin_dotnet."""
    analysis = AnalysisResult(project_id="test", runtimes={"xamarin"})
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()

    # patch Mono.Android.dll
    res = router.route("patch Mono.Android.dll", tools, analysis)
    assert "xamarin_dotnet" in res.matched_intents

    # modify assembly in project
    res = router.route("modify assembly in application", tools, analysis)
    assert "xamarin_dotnet" in res.matched_intents


def test_ui_layout_preserves_strings_xml_when_many_layouts(workspace):
    """When a workspace has >= 8 layouts, res/values/strings.xml and colors.xml must NOT be starved."""
    layout_dir = workspace.decoded_dir / "res/layout"
    layout_dir.mkdir(parents=True)
    (layout_dir / "activity_main.xml").write_text("<LinearLayout/>")
    for i in range(1, 15):
        (layout_dir / f"view_item_{i:02d}.xml").write_text(f"<View id='{i}'/>")

    values_dir = workspace.decoded_dir / "res/values"
    values_dir.mkdir(parents=True)
    (values_dir / "strings.xml").write_text(
        "<resources><string name='app_name'>App</string></resources>"
    )
    (values_dir / "colors.xml").write_text(
        "<resources><color name='primary'>#fff</color></resources>"
    )

    analysis = AnalysisResult(project_id="test", runtimes={"dalvik"})
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()

    result = router.route("change button text color in main view", tools, analysis)
    assert "ui_layout" in result.matched_intents
    assert len(result.seen_files) <= 8
    # Both strings.xml and colors.xml must be present in seen_files
    assert "res/values/strings.xml" in result.seen_files
    assert "res/values/colors.xml" in result.seen_files
    assert "res/layout/activity_main.xml" in result.seen_files


def test_composite_four_intents_fair_allocation(workspace):
    """Composite request matching 4 intents fairly allocates budget up to 20 files without starving later intents."""
    decoded = workspace.decoded_dir
    # 1. app_name files
    (decoded / "AndroidManifest.xml").write_text("<manifest package='com.example'/>")
    values_dir = decoded / "res/values"
    values_dir.mkdir(parents=True, exist_ok=True)
    (values_dir / "strings.xml").write_text("<resources/>")
    for lang in ("en", "es", "fr", "de", "it", "ja", "zh"):
        lang_dir = decoded / f"res/values-{lang}"
        lang_dir.mkdir(parents=True, exist_ok=True)
        (lang_dir / "strings.xml").write_text("<resources/>")

    # 2. ui_layout files
    layout_dir = decoded / "res/layout"
    layout_dir.mkdir(parents=True, exist_ok=True)
    for i in range(8):
        (layout_dir / f"layout_{i}.xml").write_text("<View/>")

    # 3. toast_flash files (launcher smali)
    smali_dir = decoded / "smali/com/example"
    smali_dir.mkdir(parents=True, exist_ok=True)
    (smali_dir / "MainActivity.smali").write_text(".class public Lcom/example/MainActivity;")
    for i in range(1, 8):
        (smali_dir / f"MainActivity${i}.smali").write_text(
            f".class public Lcom/example/MainActivity${i};"
        )

    # 4. network_ping files
    for i in range(8):
        (smali_dir / f"NetworkClient{i}.smali").write_text(
            f".class public Lcom/example/NetworkClient{i};"
        )

    analysis = AnalysisResult(
        project_id="test",
        package_name="com.example",
        runtimes={"dalvik"},
        components=[
            ComponentInfo(
                name="com.example.MainActivity", component_type="activity", is_launcher=True
            )
        ],
    )
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()

    req = "rename app, modify layout, show toast on click, and ping server on launch"
    result = router.route(req, tools, analysis)

    assert set(result.matched_intents) == {"app_name", "ui_layout", "toast_flash", "network_ping"}
    assert len(result.seen_files) == 20

    # Ensure EVERY matched intent has files in seen_files
    has_app_name = any("strings.xml" in p or "AndroidManifest.xml" in p for p in result.seen_files)
    has_layout = any(p.startswith("res/layout/") for p in result.seen_files)
    has_toast = any("MainActivity" in p for p in result.seen_files)
    has_network = any("NetworkClient" in p for p in result.seen_files)

    assert has_app_name, "app_name files missing"
    assert has_layout, "ui_layout files missing"
    assert has_toast, "toast_flash files missing"
    assert has_network, "network_ping files missing (starvation bug!)"


def test_manifest_fallback_when_analysis_none_with_custom_namespace(workspace):
    """When analysis is None, router safely parses AndroidManifest.xml with custom namespace prefixes."""
    manifest_content = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:custom="http://schemas.android.com/apk/res/android" package="com.custom.app">
    <application custom:label="@string/app_label">
        <activity-alias custom:name=".EntryAlias" custom:targetActivity=".ui.RealEntryActivity">
            <intent-filter>
                <action custom:name="android.intent.action.MAIN" />
                <category custom:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity-alias>
        <receiver custom:name=".receivers.AlarmTrigger" />
        <service custom:name=".services.BackgroundWorker" />
    </application>
</manifest>
"""
    (workspace.decoded_dir / "AndroidManifest.xml").write_text(manifest_content)

    # Create matching smali files
    ui_dir = workspace.decoded_dir / "smali/com/custom/app/ui"
    ui_dir.mkdir(parents=True, exist_ok=True)
    (ui_dir / "RealEntryActivity.smali").write_text(
        ".class public Lcom/custom/app/ui/RealEntryActivity;"
    )

    rec_dir = workspace.decoded_dir / "smali/com/custom/app/receivers"
    rec_dir.mkdir(parents=True, exist_ok=True)
    (rec_dir / "AlarmTrigger.smali").write_text(
        ".class public Lcom/custom/app/receivers/AlarmTrigger;"
    )

    srv_dir = workspace.decoded_dir / "smali/com/custom/app/services"
    srv_dir.mkdir(parents=True, exist_ok=True)
    (srv_dir / "BackgroundWorker.smali").write_text(
        ".class public Lcom/custom/app/services/BackgroundWorker;"
    )

    tools = AiContextTools(workspace)  # analysis is None
    router = IntentRouter()

    # 1. Launcher activity via alias targetActivity resolution
    res_toast = router.route("show toast on button click", tools, analysis=None)
    assert "toast_flash" in res_toast.matched_intents
    assert "smali/com/custom/app/ui/RealEntryActivity.smali" in res_toast.seen_files

    # 2. Receiver resolution from manifest without analysis
    res_rec = router.route("modify broadcast receiver", tools, analysis=None)
    assert "receiver_service" in res_rec.matched_intents
    assert "smali/com/custom/app/receivers/AlarmTrigger.smali" in res_rec.seen_files

    # 3. Service resolution from manifest without analysis
    res_srv = router.route("update background service", tools, analysis=None)
    assert "receiver_service" in res_srv.matched_intents
    assert "smali/com/custom/app/services/BackgroundWorker.smali" in res_srv.seen_files


def test_smali_full_package_prioritized_over_same_named_library(workspace):
    """Full package match is prioritized over another package's class with the same simple name."""
    decoded = workspace.decoded_dir
    # Library class with simple name MainActivity in package "a" (which precedes "com" alphabetically)
    lib_dir = decoded / "smali/a"
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "MainActivity.smali").write_text(".class public La/MainActivity;")

    # Real launcher activity in com.example.app
    app_dir = decoded / "smali/com/example/app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "MainActivity.smali").write_text(".class public Lcom/example/app/MainActivity;")

    analysis = AnalysisResult(
        project_id="test",
        package_name="com.example.app",
        runtimes={"dalvik"},
        components=[
            ComponentInfo(
                name="com.example.app.MainActivity", component_type="activity", is_launcher=True
            )
        ],
    )
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()

    result = router.route("show toast on click", tools, analysis)
    assert "toast_flash" in result.matched_intents
    # Real launcher must be first
    files = list(result.seen_files.keys())
    assert files[0] == "smali/com/example/app/MainActivity.smali"


def test_bcp47_and_localized_strings_xml_priority(workspace):
    """English strings.xml is prioritized over obscure alphabetic locales; BCP-47 qualifiers are recognized."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text("<manifest/>")
    values_dir = decoded / "res/values"
    values_dir.mkdir(parents=True, exist_ok=True)
    (values_dir / "strings.xml").write_text("<resources/>")

    # Create 8 locales that sort before 'en' alphabetically
    for lang in ("af", "ar", "b+sr+Latn", "ca", "cs", "da", "de", "el"):
        d = decoded / f"res/values-{lang}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "strings.xml").write_text("<resources/>")

    # Create English locale
    en_dir = decoded / "res/values-en"
    en_dir.mkdir(parents=True, exist_ok=True)
    (en_dir / "strings.xml").write_text("<resources/>")

    analysis = AnalysisResult(project_id="test", runtimes={"dalvik"})
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()

    result = router.route("change app name to Hello", tools, analysis)
    assert "app_name" in result.matched_intents
    assert len(result.seen_files) <= 8
    assert "AndroidManifest.xml" in result.seen_files
    assert "res/values/strings.xml" in result.seen_files
    # English must NOT be squeezed out by alphabetical languages
    assert "res/values-en/strings.xml" in result.seen_files


def test_empty_or_none_request_handled_cleanly(workspace):
    """Empty or None requests return no_intent_matched without crashing."""
    tools = AiContextTools(workspace)
    router = IntentRouter()

    res_none = router.route(None, tools)
    assert res_none.matched_intents == []
    assert res_none.stop_reason == "no_intent_matched"

    res_empty = router.route("", tools)
    assert res_empty.matched_intents == []
    assert res_empty.stop_reason == "no_intent_matched"

    res_spaces = router.route("   ", tools)
    assert res_spaces.matched_intents == []
    assert res_spaces.stop_reason == "no_intent_matched"


def test_runtime_case_insensitivity(workspace):
    """Runtimes in analysis metadata are matched case-insensitively."""
    analysis = AnalysisResult(project_id="test", runtimes={"Flutter", "Dalvik"})
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()

    res = router.route("modify flutter dart code", tools, analysis)
    assert "flutter_dart" in res.matched_intents


# ── Adversarial XML Namespaces & Localization Auditing ──────────────


def test_xml_namespaces_custom_prefixes_and_attributes(workspace):
    """Manifest with non-standard prefix 'noir' parses activities, receivers, and services."""
    manifest = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:noir="http://schemas.android.com/apk/res/android" package="com.custom.prefix">
    <application noir:label="@string/app_label">
        <activity-alias noir:name=".EntryAlias" noir:targetActivity=".ui.RealEntryActivity">
            <intent-filter>
                <action noir:name="android.intent.action.MAIN" />
                <category noir:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity-alias>
        <receiver noir:name=".receivers.CustomReceiver" />
        <service noir:name=".services.CustomService" />
    </application>
</manifest>
"""
    (workspace.decoded_dir / "AndroidManifest.xml").write_text(manifest)

    ui_dir = workspace.decoded_dir / "smali/com/custom/prefix/ui"
    ui_dir.mkdir(parents=True, exist_ok=True)
    (ui_dir / "RealEntryActivity.smali").write_text(
        ".class public Lcom/custom/prefix/ui/RealEntryActivity;"
    )

    rec_dir = workspace.decoded_dir / "smali/com/custom/prefix/receivers"
    rec_dir.mkdir(parents=True, exist_ok=True)
    (rec_dir / "CustomReceiver.smali").write_text(
        ".class public Lcom/custom/prefix/receivers/CustomReceiver;"
    )

    srv_dir = workspace.decoded_dir / "smali/com/custom/prefix/services"
    srv_dir.mkdir(parents=True, exist_ok=True)
    (srv_dir / "CustomService.smali").write_text(
        ".class public Lcom/custom/prefix/services/CustomService;"
    )

    tools = AiContextTools(workspace)
    router = IntentRouter()

    res_toast = router.route("show toast on click", tools)
    assert "toast_flash" in res_toast.matched_intents
    assert "smali/com/custom/prefix/ui/RealEntryActivity.smali" in res_toast.seen_files

    res_rec = router.route("register receiver for alarms", tools)
    assert "receiver_service" in res_rec.matched_intents
    assert "smali/com/custom/prefix/receivers/CustomReceiver.smali" in res_rec.seen_files


def test_xml_default_namespace_handling_bug(workspace):
    """When AndroidManifest.xml has a default xmlns, router should extract application components."""
    manifest_default_ns = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns="http://schemas.android.com/apk/res/android" xmlns:android="http://schemas.android.com/apk/res/android" package="com.defaultns.app">
    <application android:label="@string/app_name">
        <activity android:name=".EntryActivity">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
        <receiver android:name=".CustomAlarm" />
        <service android:name=".SyncService" />
    </application>
</manifest>
"""
    (workspace.decoded_dir / "AndroidManifest.xml").write_text(manifest_default_ns)
    smali_dir = workspace.decoded_dir / "smali/com/defaultns/app"
    smali_dir.mkdir(parents=True, exist_ok=True)
    (smali_dir / "EntryActivity.smali").write_text(
        ".class public Lcom/defaultns/app/EntryActivity;"
    )
    (smali_dir / "CustomAlarm.smali").write_text(".class public Lcom/defaultns/app/CustomAlarm;")
    (smali_dir / "SyncService.smali").write_text(".class public Lcom/defaultns/app/SyncService;")

    tools = AiContextTools(workspace)
    router = IntentRouter()

    res_toast = router.route("show toast on button click", tools)
    assert "toast_flash" in res_toast.matched_intents
    # Fails under current implementation because root.find("application") is None
    assert "smali/com/defaultns/app/EntryActivity.smali" in res_toast.seen_files


def test_diverse_localized_resource_qualifiers(workspace):
    """Diverse BCP-47, script, region, and mode qualifiers are matched by app_name selector."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text("<manifest/>")
    values_dir = decoded / "res/values"
    values_dir.mkdir(parents=True, exist_ok=True)
    (values_dir / "strings.xml").write_text("<resources/>")

    # Distinct complex qualifiers
    qualifiers = [
        "b+sr+Latn",  # Serbian in Latin script
        "zh-rCN",  # Chinese Simplified (China)
        "b+es+419",  # Spanish (Latin America)
        "night",  # Night mode
        "night-v8",  # Night mode API 8
    ]
    for q in qualifiers:
        d = decoded / f"res/values-{q}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "strings.xml").write_text("<resources/>")

    analysis = AnalysisResult(project_id="test", runtimes={"dalvik"})
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()

    res = router.route("rename app to NewApp", tools, analysis)
    assert "app_name" in res.matched_intents
    assert "AndroidManifest.xml" in res.seen_files
    assert "res/values/strings.xml" in res.seen_files
    assert "res/values-b+sr+Latn/strings.xml" in res.seen_files
    assert "res/values-zh-rCN/strings.xml" in res.seen_files
    assert "res/values-b+es+419/strings.xml" in res.seen_files
    assert "res/values-night/strings.xml" in res.seen_files


def test_resources_values_strings_inverted_sort_bug(workspace):
    """resources/values/strings.xml must NOT be sorted after all localized qualifiers."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text("<manifest/>")

    # Base strings in resources/values/
    res_base = decoded / "resources/values"
    res_base.mkdir(parents=True, exist_ok=True)
    (res_base / "strings.xml").write_text(
        "<resources><string name='app_name'>Base</string></resources>"
    )

    # 8 localized qualifiers
    for lang in ("af", "ar", "bg", "ca", "cs", "da", "de", "el"):
        d = decoded / f"resources/values-{lang}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "strings.xml").write_text("<resources/>")

    analysis = AnalysisResult(project_id="test", runtimes={"dalvik"})
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()

    res = router.route("rename app to SuperApp", tools, analysis)
    assert "app_name" in res.matched_intents
    # Under current code, '-' < '/' sorts resources/values/strings.xml to the end (dropped by cap 8)
    assert "resources/values/strings.xml" in res.seen_files


def test_obfuscated_flattened_smali_classes_resolution(workspace):
    """Single-letter flattened smali classes with full package matching are resolved."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.obf">\n'
        "  <application>\n"
        '    <activity android:name="a.b.c">\n'
        "      <intent-filter>\n"
        '        <action android:name="android.intent.action.MAIN"/>\n'
        '        <category android:name="android.intent.category.LAUNCHER"/>\n'
        "      </intent-filter>\n"
        "    </activity>\n"
        "  </application>\n"
        "</manifest>"
    )
    smali_dir = decoded / "smali/a/b"
    smali_dir.mkdir(parents=True, exist_ok=True)
    (smali_dir / "c.smali").write_text(".class public La/b/c;")

    tools = AiContextTools(workspace)
    router = IntentRouter()

    res = router.route("show toast on button click", tools)
    assert "toast_flash" in res.matched_intents
    assert "smali/a/b/c.smali" in res.seen_files


def test_multidex_smali_classes_directories_handling(workspace):
    """Launcher activities and services in secondary dex directories (smali_classes2, smali_classes3) are found."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text(
        '<manifest package="com.multidex">\n'
        "  <application>\n"
        '    <activity android:name="com.multidex.ui.SplashActivity">\n'
        "      <intent-filter>\n"
        '        <action android:name="android.intent.action.MAIN"/>\n'
        '        <category android:name="android.intent.category.LAUNCHER"/>\n'
        "      </intent-filter>\n"
        "    </activity>\n"
        '    <service android:name="com.multidex.services.DataService" />\n'
        "  </application>\n"
        "</manifest>"
    )
    # Launcher in smali_classes2
    dex2_dir = decoded / "smali_classes2/com/multidex/ui"
    dex2_dir.mkdir(parents=True, exist_ok=True)
    (dex2_dir / "SplashActivity.smali").write_text(".class public Lcom/multidex/ui/SplashActivity;")

    # Service in smali_classes3
    dex3_dir = decoded / "smali_classes3/com/multidex/services"
    dex3_dir.mkdir(parents=True, exist_ok=True)
    (dex3_dir / "DataService.smali").write_text(".class public Lcom/multidex/services/DataService;")

    tools = AiContextTools(workspace)
    router = IntentRouter()

    res_toast = router.route("show toast on click", tools)
    assert "toast_flash" in res_toast.matched_intents
    assert "smali_classes2/com/multidex/ui/SplashActivity.smali" in res_toast.seen_files

    res_srv = router.route("modify background service", tools)
    assert "receiver_service" in res_srv.matched_intents
    assert "smali_classes3/com/multidex/services/DataService.smali" in res_srv.seen_files


def test_receiver_service_priority_inversion_starvation_bug(workspace):
    """Manifest-declared receiver must NOT be starved by short SDK service classes."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.mycompany.app">\n'
        "  <application>\n"
        '    <receiver android:name="com.mycompany.app.receivers.AppBroadcastReceiver" />\n'
        "  </application>\n"
        "</manifest>"
    )
    # Real receiver with long package path
    app_dir = decoded / "smali/com/mycompany/app/receivers"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "AppBroadcastReceiver.smali").write_text(
        ".class public Lcom/mycompany/app/receivers/AppBroadcastReceiver;"
    )

    # 8 short SDK service classes
    sdk_dir = decoded / "smali/sdk"
    sdk_dir.mkdir(parents=True, exist_ok=True)
    for i in range(8):
        (sdk_dir / f"Service{i}.smali").write_text(f".class public Lsdk/Service{i};")

    tools = AiContextTools(workspace)
    router = IntentRouter()

    res = router.route("modify broadcast receiver", tools)
    assert "receiver_service" in res.matched_intents
    # Fails under current code because len(Service{i}.smali) < len(AppBroadcastReceiver.smali)
    assert "smali/com/mycompany/app/receivers/AppBroadcastReceiver.smali" in res.seen_files


def test_obfuscated_zero_file_match_bypasses_discovery_bug(workspace):
    """When intent triggers but 0 candidate files are found, router should fall back to AI discovery."""
    decoded = workspace.decoded_dir
    # Obfuscated project where smali names do not match launcher keywords
    smali_dir = decoded / "smali/a/b"
    smali_dir.mkdir(parents=True, exist_ok=True)
    (smali_dir / "x.smali").write_text(".class public La/b/x;")

    tools = AiContextTools(workspace)
    router = IntentRouter()

    res = router.route("show toast on button click", tools)
    # If 0 files found, stop_reason should be no_intent_matched / fall through, not intent_matched
    if len(res.seen_files) == 0:
        assert res.stop_reason == "no_intent_matched", (
            "Router falsely claims 'intent_matched' with 0 files, which prevents AI discovery fallback"
        )


def test_obfuscated_single_letter_relative_name_greedy_match_bug(workspace):
    """Relative class '.a' must not greedily match all smali paths or prioritize library 'androidx/core/a.smali'."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text(
        '<manifest package="com.mycompany.app">\n'
        "  <application>\n"
        '    <activity android:name=".a">\n'
        "      <intent-filter>\n"
        '        <action android:name="android.intent.action.MAIN"/>\n'
        '        <category android:name="android.intent.category.LAUNCHER"/>\n'
        "      </intent-filter>\n"
        "    </activity>\n"
        "  </application>\n"
        "</manifest>"
    )
    # The real app class
    app_dir = decoded / "smali/com/mycompany/app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "a.smali").write_text(".class public Lcom/mycompany/app/a;")

    # Third-party library class that sorts before 'com'
    lib_dir = decoded / "smali/androidx/core"
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "a.smali").write_text(".class public Landroidx/core/a;")

    # Unrelated class
    unrelated_dir = decoded / "smali/com/unrelated"
    unrelated_dir.mkdir(parents=True, exist_ok=True)
    (unrelated_dir / "worker.smali").write_text(".class public Lcom/unrelated/worker;")

    tools = AiContextTools(workspace)
    router = IntentRouter()

    res = router.route("show toast on button click", tools)
    assert "toast_flash" in res.matched_intents
    # The app's own class must be first, not androidx/core/a.smali
    first_file = list(res.seen_files.keys())[0]
    assert first_file == "smali/com/mycompany/app/a.smali"
    # Unrelated worker.smali must NOT be matched just because 'smali/' contains 'a'
    assert "smali/com/unrelated/worker.smali" not in res.seen_files


# ── Adversarial Cross-Framework Runtime Detection & Scoping ──────────


def test_unity_mono_vs_il2cpp_metadata_discrimination(workspace):
    """With analysis metadata specified, generic 'modify unity' prompt strictly discriminates Mono vs IL2CPP."""
    decoded = workspace.decoded_dir
    # Create both sets of files in the workspace
    managed_dir = decoded / "assets/bin/Data/Managed"
    managed_dir.mkdir(parents=True, exist_ok=True)
    (managed_dir / "Assembly-CSharp.dll").write_bytes(b"MONO_DLL")
    (decoded / "lib/arm64-v8a").mkdir(parents=True, exist_ok=True)
    (decoded / "lib/arm64-v8a/libmono.so").write_bytes(b"ELF_MONO")
    (decoded / "lib/arm64-v8a/libil2cpp.so").write_bytes(b"ELF_IL2CPP")
    meta_dir = decoded / "assets/bin/Data/Managed/etc/metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "global-metadata.dat").write_bytes(b"METADATA")

    router = IntentRouter()

    # 1. When metadata says 'mono'
    analysis_mono = AnalysisResult(project_id="test", runtimes={"mono"})
    tools_mono = AiContextTools(workspace, analysis_mono)
    res_mono = router.route("modify unity game logic", tools_mono, analysis_mono)
    assert res_mono.matched_intents == ["unity_mono"]
    assert "assets/bin/Data/Managed/Assembly-CSharp.dll" in res_mono.seen_files
    assert "lib/arm64-v8a/libmono.so" in res_mono.seen_files
    assert "lib/arm64-v8a/libil2cpp.so" not in res_mono.seen_files
    assert "assets/bin/Data/Managed/etc/metadata/global-metadata.dat" not in res_mono.seen_files

    # 2. When metadata says 'il2cpp'
    analysis_il2cpp = AnalysisResult(project_id="test", runtimes={"il2cpp"})
    tools_il2cpp = AiContextTools(workspace, analysis_il2cpp)
    res_il2cpp = router.route("modify unity game logic", tools_il2cpp, analysis_il2cpp)
    assert res_il2cpp.matched_intents == ["unity_il2cpp"]
    assert "lib/arm64-v8a/libil2cpp.so" in res_il2cpp.seen_files
    assert "assets/bin/Data/Managed/etc/metadata/global-metadata.dat" in res_il2cpp.seen_files
    assert "assets/bin/Data/Managed/Assembly-CSharp.dll" not in res_il2cpp.seen_files
    assert "lib/arm64-v8a/libmono.so" not in res_il2cpp.seen_files


def test_unity_runtime_scoping_blocks_cross_runtime_intents(workspace):
    """Runtime scoping rejects Mono-specific intents on IL2CPP apps and IL2CPP intents on Mono apps."""
    router = IntentRouter()

    # Mono app
    analysis_mono = AnalysisResult(project_id="test", runtimes={"mono"})
    tools_mono = AiContextTools(workspace, analysis_mono)
    res_cross_il2cpp = router.route(
        "patch libil2cpp.so binary and global-metadata.dat", tools_mono, analysis_mono
    )
    assert res_cross_il2cpp.matched_intents == []
    assert res_cross_il2cpp.stop_reason == "no_intent_matched"

    # IL2CPP app
    analysis_il2cpp = AnalysisResult(project_id="test", runtimes={"il2cpp"})
    tools_il2cpp = AiContextTools(workspace, analysis_il2cpp)
    res_cross_mono = router.route(
        "modify Assembly-CSharp.dll in assets", tools_il2cpp, analysis_il2cpp
    )
    assert res_cross_mono.matched_intents == []
    assert res_cross_mono.stop_reason == "no_intent_matched"


def test_unity_mono_raw_directory_marker_discrimination(workspace):
    """Raw directory marker scanning detects 'mono' and NOT 'il2cpp' for standard Unity Mono project."""
    decoded = workspace.decoded_dir
    managed_dir = decoded / "assets/bin/Data/Managed"
    managed_dir.mkdir(parents=True, exist_ok=True)
    (managed_dir / "Assembly-CSharp.dll").write_bytes(b"MONO_DLL")
    (decoded / "lib/arm64-v8a").mkdir(parents=True, exist_ok=True)
    (decoded / "lib/arm64-v8a/libmono.so").write_bytes(b"ELF_MONO")

    tools = AiContextTools(workspace)
    runtimes = scan_directory_markers(tools)
    assert "mono" in runtimes
    assert "il2cpp" not in runtimes

    router = IntentRouter()
    res = router.route("modify unity script", tools)
    assert res.matched_intents == ["unity_mono"]
    assert "assets/bin/Data/Managed/Assembly-CSharp.dll" in res.seen_files


def test_unity_il2cpp_raw_directory_marker_discrimination_defect(workspace):
    """Raw directory marker scanning on Unity IL2CPP must NOT falsely detect 'mono'."""
    decoded = workspace.decoded_dir
    (decoded / "lib/arm64-v8a").mkdir(parents=True, exist_ok=True)
    (decoded / "lib/arm64-v8a/libil2cpp.so").write_bytes(b"ELF_IL2CPP")
    meta_dir = decoded / "assets/bin/Data/Managed/etc/metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "global-metadata.dat").write_bytes(b"METADATA")

    tools = AiContextTools(workspace)
    runtimes = scan_directory_markers(tools)
    assert "il2cpp" in runtimes
    # Defect: currently fails because 'assets/bin/data/managed' is in the path of global-metadata.dat
    assert "mono" not in runtimes, "IL2CPP workspace falsely detected as Mono runtime"

    router = IntentRouter()
    res = router.route("modify unity game logic", tools)
    assert res.matched_intents == ["unity_il2cpp"]


def test_react_native_vanilla_vs_hermes_discrimination(workspace):
    """Vanilla React Native (no Hermes) detects react_native but not hermes; Hermes app detects both."""
    decoded = workspace.decoded_dir
    # 1. Vanilla RN
    (decoded / "assets").mkdir(parents=True, exist_ok=True)
    (decoded / "assets/index.android.bundle").write_text("console.log('vanilla RN');")
    (decoded / "lib/arm64-v8a").mkdir(parents=True, exist_ok=True)
    (decoded / "lib/arm64-v8a/libreactnativejni.so").write_bytes(b"RN_JNI")
    (decoded / "AndroidManifest.xml").write_text("<manifest/>")

    tools_vanilla = AiContextTools(workspace)
    runtimes_vanilla = scan_directory_markers(tools_vanilla)
    assert "react_native" in runtimes_vanilla
    assert "hermes" not in runtimes_vanilla

    router = IntentRouter()
    res_vanilla = router.route("update react native js bundle", tools_vanilla)
    assert "react_native_js" in res_vanilla.matched_intents
    assert "assets/index.android.bundle" in res_vanilla.seen_files
    assert not any("libhermes" in p for p in res_vanilla.seen_files)

    # 2. Add Hermes binary
    (decoded / "lib/arm64-v8a/libhermes.so").write_bytes(b"ELF_HERMES")
    tools_hermes = AiContextTools(workspace)
    runtimes_hermes = scan_directory_markers(tools_hermes)
    assert "hermes" in runtimes_hermes
    assert "react_native" in runtimes_hermes

    res_hermes = router.route("update hermes bytecode in index.android.bundle", tools_hermes)
    assert "react_native_js" in res_hermes.matched_intents
    assert "assets/index.android.bundle" in res_hermes.seen_files
    assert "lib/arm64-v8a/libhermes.so" in res_hermes.seen_files


def test_react_native_scoping_on_pure_dalvik_app(workspace):
    """React Native / Hermes intents are rejected on pure Dalvik applications."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text("<manifest/>")
    smali_dir = decoded / "smali/com/example"
    smali_dir.mkdir(parents=True, exist_ok=True)
    (smali_dir / "MainActivity.smali").write_text(".class public Lcom/example/MainActivity;")

    analysis = AnalysisResult(project_id="test", runtimes={"dalvik"})
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()

    res_rn = router.route("modify react native js bundle", tools, analysis)
    assert res_rn.matched_intents == []
    assert res_rn.stop_reason == "no_intent_matched"

    res_hermes = router.route("update hermes bytecode in index.android.bundle", tools, analysis)
    assert res_hermes.matched_intents == []
    assert res_hermes.stop_reason == "no_intent_matched"


def test_react_native_main_jsbundle_marker_detection_defect(workspace):
    """React Native projects using main.jsbundle must be detected during marker scanning."""
    decoded = workspace.decoded_dir
    (decoded / "assets").mkdir(parents=True, exist_ok=True)
    (decoded / "assets/main.jsbundle").write_text("console.log('main bundle');")
    (decoded / "AndroidManifest.xml").write_text("<manifest/>")

    tools = AiContextTools(workspace)
    runtimes = scan_directory_markers(tools)
    assert "react_native" in runtimes, "main.jsbundle not recognized by scan_directory_markers"

    res = IntentRouter().route("modify react native js bundle", tools)
    assert "react_native_js" in res.matched_intents
    assert "assets/main.jsbundle" in res.seen_files


def test_xamarin_dotnet_assemblies_resolution_and_scoping(workspace):
    """Xamarin assemblies in assemblies/ directory are resolved and scoped away from Dalvik and Unity."""
    decoded = workspace.decoded_dir
    asm_dir = decoded / "assemblies"
    asm_dir.mkdir(parents=True, exist_ok=True)
    (asm_dir / "MyApp.dll").write_bytes(b"DLL1")
    (asm_dir / "Mono.Android.dll").write_bytes(b"DLL2")
    (asm_dir / "Xamarin.Forms.Core.dll").write_bytes(b"DLL3")

    analysis = AnalysisResult(project_id="test", runtimes={"xamarin"})
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()

    res = router.route("modify xamarin .net assemblies", tools, analysis)
    assert res.matched_intents == ["xamarin_dotnet"]
    assert "assemblies/MyApp.dll" in res.seen_files
    assert "assemblies/Mono.Android.dll" in res.seen_files
    assert "assemblies/Xamarin.Forms.Core.dll" in res.seen_files

    # Scoping: Dalvik app rejects xamarin
    tools_dalvik = AiContextTools(workspace, AnalysisResult(project_id="test", runtimes={"dalvik"}))
    assert (
        router.route(
            "modify xamarin .net assemblies", tools_dalvik, tools_dalvik.analysis
        ).matched_intents
        == []
    )

    # Scoping: Unity Mono app rejects xamarin
    tools_mono = AiContextTools(workspace, AnalysisResult(project_id="test", runtimes={"mono"}))
    assert (
        router.route(
            "modify xamarin .net assemblies", tools_mono, tools_mono.analysis
        ).matched_intents
        == []
    )


def test_xamarin_monodroid_marker_collision_with_unity_mono_defect(workspace):
    """Xamarin runtime library libmonodroid.so must NOT falsely trigger Unity Mono detection or selection."""
    decoded = workspace.decoded_dir
    (decoded / "assemblies").mkdir(parents=True, exist_ok=True)
    (decoded / "assemblies/App.dll").write_bytes(b"DLL")
    (decoded / "lib/arm64-v8a").mkdir(parents=True, exist_ok=True)
    (decoded / "lib/arm64-v8a/libmonodroid.so").write_bytes(b"ELF_MONODROID")

    tools = AiContextTools(workspace)
    runtimes = scan_directory_markers(tools)
    assert "xamarin" in runtimes
    # Defect: "libmono" in "lib/arm64-v8a/libmonodroid.so" evaluates to True!
    assert "mono" not in runtimes, "libmonodroid.so falsely triggered Unity Mono runtime detection"

    router = IntentRouter()
    res = router.route("modify unity c# script", tools)
    assert "unity_mono" not in res.matched_intents, "Unity Mono intent matched on pure Xamarin app"


def test_native_elf_scoping_pure_dalvik_vs_native_presence(workspace):
    """Native ELF intents trigger when .so files exist, but are completely blocked on pure Dalvik apps."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text("<manifest/>")
    smali_dir = decoded / "smali/com/example"
    smali_dir.mkdir(parents=True, exist_ok=True)
    (smali_dir / "MainActivity.smali").write_text(".class public Lcom/example/MainActivity;")

    # 1. Pure Dalvik (no .so files)
    tools_dalvik = AiContextTools(workspace)
    router = IntentRouter()
    for prompt in (
        "patch native .so library",
        "modify c++ function in native library",
        "hook jni function call",
    ):
        res = router.route(prompt, tools_dalvik)
        assert res.matched_intents == []
        assert res.stop_reason == "no_intent_matched"

    # 2. Add native libraries under lib/
    (decoded / "lib/arm64-v8a").mkdir(parents=True, exist_ok=True)
    (decoded / "lib/arm64-v8a/libcore.so").write_bytes(b"ELF64")
    (decoded / "lib/armeabi-v7a").mkdir(parents=True, exist_ok=True)
    (decoded / "lib/armeabi-v7a/libcore.so").write_bytes(b"ELF32")

    tools_native = AiContextTools(workspace)
    runtimes = scan_directory_markers(tools_native)
    assert "native" in runtimes

    res_native = router.route("patch native .so library", tools_native)
    assert res_native.matched_intents == ["native_elf"]
    assert "lib/arm64-v8a/libcore.so" in res_native.seen_files
    assert "lib/armeabi-v7a/libcore.so" in res_native.seen_files


def test_hybrid_flutter_and_native_cpp_plugin(workspace):
    """Hybrid Flutter app with native C++ plugin routes both intents cleanly without cross-pollution."""
    decoded = workspace.decoded_dir
    (decoded / "lib/arm64-v8a").mkdir(parents=True, exist_ok=True)
    (decoded / "lib/arm64-v8a/libapp.so").write_bytes(b"LIBAPP")
    (decoded / "lib/arm64-v8a/libflutter.so").write_bytes(b"LIBFLUTTER")
    (decoded / "lib/arm64-v8a/libcustom_plugin.so").write_bytes(b"LIBPLUGIN")
    (decoded / "assets/flutter_assets").mkdir(parents=True, exist_ok=True)
    (decoded / "assets/flutter_assets/kernel_blob.bin").write_bytes(b"DART")
    (decoded / "smali/com/example").mkdir(parents=True, exist_ok=True)
    (decoded / "smali/com/example/MainActivity.smali").write_text(
        ".class public Lcom/example/MainActivity;"
    )

    analysis = AnalysisResult(project_id="test", runtimes={"flutter", "native", "dalvik"})
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()

    # Prompt triggers both flutter and native_elf
    res = router.route("modify flutter dart code and patch native c++ plugin", tools, analysis)
    assert set(res.matched_intents) == {"flutter_dart", "native_elf"}
    assert "lib/arm64-v8a/libapp.so" in res.seen_files
    assert "lib/arm64-v8a/libflutter.so" in res.seen_files
    assert "lib/arm64-v8a/libcustom_plugin.so" in res.seen_files
    assert "assets/flutter_assets/kernel_blob.bin" in res.seen_files
    assert len(res.seen_files) == len(set(res.seen_files.keys()))

    # Unrelated framework intents are rejected
    assert router.route("modify react native js bundle", tools, analysis).matched_intents == []
    assert router.route("modify xamarin .net assemblies", tools, analysis).matched_intents == []
    assert router.route("modify unity c# script", tools, analysis).matched_intents == []


def test_hybrid_unity_with_java_wrappers(workspace):
    """Unity IL2CPP app with Java launcher and AndroidManifest routes app_name, toast, and il2cpp together."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text(
        '<manifest package="com.unity.game">\n'
        '  <application android:label="@string/app_name">\n'
        '    <activity android:name="com.unity3d.player.UnityPlayerActivity">\n'
        "      <intent-filter>\n"
        '        <action android:name="android.intent.action.MAIN"/>\n'
        '        <category android:name="android.intent.category.LAUNCHER"/>\n'
        "      </intent-filter>\n"
        "    </activity>\n"
        "  </application>\n"
        "</manifest>"
    )
    (decoded / "res/values").mkdir(parents=True, exist_ok=True)
    (decoded / "res/values/strings.xml").write_text(
        "<resources><string name='app_name'>UnityGame</string></resources>"
    )
    (decoded / "smali/com/unity3d/player").mkdir(parents=True, exist_ok=True)
    (decoded / "smali/com/unity3d/player/UnityPlayerActivity.smali").write_text(
        ".class public Lcom/unity3d/player/UnityPlayerActivity;"
    )
    (decoded / "lib/arm64-v8a").mkdir(parents=True, exist_ok=True)
    (decoded / "lib/arm64-v8a/libil2cpp.so").write_bytes(b"ELF_IL2CPP")
    (decoded / "assets/bin/Data/Managed/etc/metadata").mkdir(parents=True, exist_ok=True)
    (decoded / "assets/bin/Data/Managed/etc/metadata/global-metadata.dat").write_bytes(b"METADATA")

    analysis = AnalysisResult(
        project_id="test",
        package_name="com.unity.game",
        runtimes={"il2cpp", "dalvik", "native"},
        components=[
            ComponentInfo(
                name="com.unity3d.player.UnityPlayerActivity",
                component_type="activity",
                is_launcher=True,
            )
        ],
    )
    tools = AiContextTools(workspace, analysis)
    router = IntentRouter()

    req = "rename app, show toast on click, and patch unity il2cpp"
    res = router.route(req, tools, analysis)
    assert set(res.matched_intents) == {"app_name", "toast_flash", "unity_il2cpp"}
    assert "AndroidManifest.xml" in res.seen_files
    assert "res/values/strings.xml" in res.seen_files
    assert "smali/com/unity3d/player/UnityPlayerActivity.smali" in res.seen_files
    assert "lib/arm64-v8a/libil2cpp.so" in res.seen_files
    assert "assets/bin/Data/Managed/etc/metadata/global-metadata.dat" in res.seen_files


def test_comprehensive_runtime_scoping_isolation_matrix(workspace):
    """Exhaustive check that each runtime-specific intent rejects non-matching runtimes."""
    tools = AiContextTools(workspace)
    router = IntentRouter()

    # (intent_prompt, intent_id, allowed_runtime, disallowed_runtimes)
    matrix = [
        (
            "show a toast on click",
            "toast_flash",
            "dalvik",
            ["native_only", "flutter", "react_native"],
        ),
        (
            "ping endpoint on launch",
            "network_ping",
            "dalvik",
            ["native_only", "flutter", "react_native"],
        ),
        (
            "change button text in layout",
            "ui_layout",
            "dalvik",
            ["native_only", "flutter", "react_native"],
        ),
        (
            "add broadcast receiver for boot",
            "receiver_service",
            "dalvik",
            ["native_only", "flutter", "react_native"],
        ),
        (
            "modify react native js bundle",
            "react_native_js",
            "react_native",
            ["dalvik", "flutter", "mono", "xamarin"],
        ),
        (
            "modify flutter dart code",
            "flutter_dart",
            "flutter",
            ["dalvik", "react_native", "mono", "xamarin"],
        ),
        (
            "modify unity c# script",
            "unity_mono",
            "mono",
            ["dalvik", "react_native", "flutter", "xamarin"],
        ),
        (
            "patch libil2cpp.so binary",
            "unity_il2cpp",
            "il2cpp",
            ["dalvik", "react_native", "flutter", "mono", "xamarin"],
        ),
        ("patch native .so library", "native_elf", "native", ["dalvik"]),
        (
            "modify xamarin .net assemblies",
            "xamarin_dotnet",
            "xamarin",
            ["dalvik", "react_native", "flutter", "mono"],
        ),
    ]

    for prompt, intent_id, _allowed, disallowed_list in matrix:
        for disallowed in disallowed_list:
            analysis = AnalysisResult(project_id="test", runtimes={disallowed})
            res = router.route(prompt, tools, analysis)
            assert intent_id not in res.matched_intents, (
                f"Intent {intent_id} matched on disallowed runtime {disallowed} for prompt {prompt!r}"
            )


# ── Reviewer B: Multi-Intent Composite Stress Testing (3+ Concurrent Broad Intents) ──


def test_composite_three_concurrent_broad_intents(workspace):
    """Composite request matching 3 broad intents (app_name, toast_flash, permission)."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.comp3">\n'
        '  <application android:label="@string/app_name">\n'
        '    <activity android:name="com.comp3.MainActivity">\n'
        "      <intent-filter>\n"
        '        <action android:name="android.intent.action.MAIN"/>\n'
        '        <category android:name="android.intent.category.LAUNCHER"/>\n'
        "      </intent-filter>\n"
        "    </activity>\n"
        "  </application>\n"
        "</manifest>"
    )
    val_dir = decoded / "res/values"
    val_dir.mkdir(parents=True, exist_ok=True)
    (val_dir / "strings.xml").write_text(
        "<resources><string name='app_name'>Comp3</string></resources>"
    )
    val_en = decoded / "res/values-en"
    val_en.mkdir(parents=True, exist_ok=True)
    (val_en / "strings.xml").write_text(
        "<resources><string name='app_name'>Comp3 EN</string></resources>"
    )

    smali_dir = decoded / "smali/com/comp3"
    smali_dir.mkdir(parents=True, exist_ok=True)
    (smali_dir / "MainActivity.smali").write_text(".class public Lcom/comp3/MainActivity;")

    tools = AiContextTools(workspace)
    analysis = AnalysisResult(
        project_id="test_comp3", package_name="com.comp3", runtimes={"dalvik"}
    )
    router = IntentRouter()

    req = "rename the app to NewComp, show toast on click, and grant camera permission"
    res = router.route(req, tools, analysis)

    assert set(res.matched_intents) == {"app_name", "toast_flash", "permission"}
    assert "AndroidManifest.xml" in res.seen_files
    assert "res/values/strings.xml" in res.seen_files
    assert "smali/com/comp3/MainActivity.smali" in res.seen_files
    assert len(res.seen_files) <= 20
    assert len(res.seen_files) == len(set(res.seen_files.keys()))


def test_composite_four_concurrent_broad_intents(workspace):
    """Composite request matching 4 broad intents (app_name, toast_flash, network_ping, ui_layout)."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.comp4">\n'
        '  <application android:label="@string/app_name">\n'
        '    <activity android:name="com.comp4.MainActivity">\n'
        "      <intent-filter>\n"
        '        <action android:name="android.intent.action.MAIN"/>\n'
        '        <category android:name="android.intent.category.LAUNCHER"/>\n'
        "      </intent-filter>\n"
        "    </activity>\n"
        "  </application>\n"
        "</manifest>"
    )
    val_dir = decoded / "res/values"
    val_dir.mkdir(parents=True, exist_ok=True)
    (val_dir / "strings.xml").write_text(
        "<resources><string name='app_name'>Comp4</string></resources>"
    )
    (val_dir / "colors.xml").write_text(
        "<resources><color name='primary'>#ff0000</color></resources>"
    )

    lay_dir = decoded / "res/layout"
    lay_dir.mkdir(parents=True, exist_ok=True)
    (lay_dir / "activity_main.xml").write_text("<LinearLayout/>")
    (lay_dir / "custom_view.xml").write_text("<TextView/>")

    smali_dir = decoded / "smali/com/comp4"
    smali_dir.mkdir(parents=True, exist_ok=True)
    (smali_dir / "MainActivity.smali").write_text(".class public Lcom/comp4/MainActivity;")
    (smali_dir / "HttpClient.smali").write_text(".class public Lcom/comp4/HttpClient;")

    tools = AiContextTools(workspace)
    analysis = AnalysisResult(
        project_id="test_comp4", package_name="com.comp4", runtimes={"dalvik"}
    )
    router = IntentRouter()

    req = "rename application, show toast on button tap, send http request to server on startup, and modify screen layout"
    res = router.route(req, tools, analysis)

    assert set(res.matched_intents) == {"app_name", "toast_flash", "network_ping", "ui_layout"}
    assert "AndroidManifest.xml" in res.seen_files
    assert "smali/com/comp4/MainActivity.smali" in res.seen_files
    assert "smali/com/comp4/HttpClient.smali" in res.seen_files
    assert "res/layout/activity_main.xml" in res.seen_files
    assert len(res.seen_files) <= 20
    assert len(res.seen_files) == len(set(res.seen_files.keys()))


def test_composite_five_concurrent_broad_intents(workspace):
    """Composite request matching 5 broad intents (app_name, toast_flash, network_ping, ui_layout, permission)."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.comp5">\n'
        '  <application android:label="@string/app_name">\n'
        '    <activity android:name="com.comp5.MainActivity">\n'
        "      <intent-filter>\n"
        '        <action android:name="android.intent.action.MAIN"/>\n'
        '        <category android:name="android.intent.category.LAUNCHER"/>\n'
        "      </intent-filter>\n"
        "    </activity>\n"
        "  </application>\n"
        "</manifest>"
    )
    val_dir = decoded / "res/values"
    val_dir.mkdir(parents=True, exist_ok=True)
    (val_dir / "strings.xml").write_text(
        "<resources><string name='app_name'>Comp5</string></resources>"
    )
    (val_dir / "colors.xml").write_text("<resources><color name='bg'>#00ff00</color></resources>")

    lay_dir = decoded / "res/layout"
    lay_dir.mkdir(parents=True, exist_ok=True)
    (lay_dir / "activity_main.xml").write_text("<LinearLayout/>")

    smali_dir = decoded / "smali/com/comp5"
    smali_dir.mkdir(parents=True, exist_ok=True)
    (smali_dir / "MainActivity.smali").write_text(".class public Lcom/comp5/MainActivity;")
    (smali_dir / "ApiService.smali").write_text(".class public Lcom/comp5/ApiService;")

    tools = AiContextTools(workspace)
    analysis = AnalysisResult(
        project_id="test_comp5", package_name="com.comp5", runtimes={"dalvik"}
    )
    router = IntentRouter()

    req = "rename app, show toast on button tap, send http post to server on start, change button text color, and grant camera permission"
    res = router.route(req, tools, analysis)

    assert set(res.matched_intents) == {
        "app_name",
        "toast_flash",
        "network_ping",
        "ui_layout",
        "permission",
    }
    assert len(res.seen_files) <= 20
    assert len(res.seen_files) == len(set(res.seen_files.keys()))
    assert "AndroidManifest.xml" in res.seen_files
    assert "smali/com/comp5/MainActivity.smali" in res.seen_files
    assert "smali/com/comp5/ApiService.smali" in res.seen_files
    assert "res/layout/activity_main.xml" in res.seen_files
    assert "res/values/strings.xml" in res.seen_files


def test_composite_six_concurrent_broad_intents_dalvik(workspace):
    """Composite request matching 6 broad intents spanning Dalvik features."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.comp6">\n'
        '  <application android:label="@string/app_name">\n'
        '    <activity android:name="com.comp6.MainActivity">\n'
        "      <intent-filter>\n"
        '        <action android:name="android.intent.action.MAIN"/>\n'
        '        <category android:name="android.intent.category.LAUNCHER"/>\n'
        "      </intent-filter>\n"
        "    </activity>\n"
        '    <receiver android:name="com.comp6.BootReceiver"/>\n'
        "  </application>\n"
        "</manifest>"
    )
    val_dir = decoded / "res/values"
    val_dir.mkdir(parents=True, exist_ok=True)
    (val_dir / "strings.xml").write_text(
        "<resources><string name='app_name'>Comp6</string></resources>"
    )
    (val_dir / "colors.xml").write_text("<resources><color name='c'>#123456</color></resources>")

    lay_dir = decoded / "res/layout"
    lay_dir.mkdir(parents=True, exist_ok=True)
    (lay_dir / "activity_main.xml").write_text("<LinearLayout/>")

    smali_dir = decoded / "smali/com/comp6"
    smali_dir.mkdir(parents=True, exist_ok=True)
    (smali_dir / "MainActivity.smali").write_text(".class public Lcom/comp6/MainActivity;")
    (smali_dir / "HttpClient.smali").write_text(".class public Lcom/comp6/HttpClient;")
    (smali_dir / "BootReceiver.smali").write_text(".class public Lcom/comp6/BootReceiver;")

    tools = AiContextTools(workspace)
    analysis = AnalysisResult(
        project_id="test_comp6", package_name="com.comp6", runtimes={"dalvik"}
    )
    router = IntentRouter()

    req = (
        "rename the app, show toast on button click, ping server on launch, "
        "update view style in layout xml, add permission android.permission.CAMERA, "
        "and register broadcast receiver on boot"
    )
    res = router.route(req, tools, analysis)

    assert set(res.matched_intents) == {
        "app_name",
        "toast_flash",
        "network_ping",
        "ui_layout",
        "permission",
        "receiver_service",
    }
    assert len(res.seen_files) <= 20
    assert len(res.seen_files) == len(set(res.seen_files.keys()))
    assert "AndroidManifest.xml" in res.seen_files
    assert "smali/com/comp6/MainActivity.smali" in res.seen_files
    assert "smali/com/comp6/HttpClient.smali" in res.seen_files
    assert "res/layout/activity_main.xml" in res.seen_files
    assert "smali/com/comp6/BootReceiver.smali" in res.seen_files


def test_composite_seven_concurrent_broad_intents_multi_runtime(workspace):
    """Composite request matching 7 broad intents across Dalvik and Native runtimes."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.comp7">\n'
        '  <application android:label="@string/app_name">\n'
        '    <activity android:name="com.comp7.MainActivity">\n'
        "      <intent-filter>\n"
        '        <action android:name="android.intent.action.MAIN"/>\n'
        '        <category android:name="android.intent.category.LAUNCHER"/>\n'
        "      </intent-filter>\n"
        "    </activity>\n"
        '    <service android:name="com.comp7.SyncService"/>\n'
        "  </application>\n"
        "</manifest>"
    )
    val_dir = decoded / "res/values"
    val_dir.mkdir(parents=True, exist_ok=True)
    (val_dir / "strings.xml").write_text(
        "<resources><string name='app_name'>Comp7</string></resources>"
    )
    lay_dir = decoded / "res/layout"
    lay_dir.mkdir(parents=True, exist_ok=True)
    (lay_dir / "activity_main.xml").write_text("<LinearLayout/>")

    smali_dir = decoded / "smali/com/comp7"
    smali_dir.mkdir(parents=True, exist_ok=True)
    (smali_dir / "MainActivity.smali").write_text(".class public Lcom/comp7/MainActivity;")
    (smali_dir / "RetrofitClient.smali").write_text(".class public Lcom/comp7/RetrofitClient;")
    (smali_dir / "SyncService.smali").write_text(".class public Lcom/comp7/SyncService;")

    lib_dir = decoded / "lib/arm64-v8a"
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "libcrypto_jni.so").write_bytes(b"ELF_CRYPTO")

    tools = AiContextTools(workspace)
    analysis = AnalysisResult(
        project_id="test_comp7", package_name="com.comp7", runtimes={"dalvik", "native"}
    )
    router = IntentRouter()

    req = (
        "rename app, show toast on click, ping endpoint on launch, change layout view, "
        "grant permission, add background service, and patch native jni library"
    )
    res = router.route(req, tools, analysis)

    assert set(res.matched_intents) == {
        "app_name",
        "toast_flash",
        "network_ping",
        "ui_layout",
        "permission",
        "receiver_service",
        "native_elf",
    }
    assert len(res.seen_files) <= 20
    assert len(res.seen_files) == len(set(res.seen_files.keys()))
    assert "lib/arm64-v8a/libcrypto_jni.so" in res.seen_files
    assert "smali/com/comp7/SyncService.smali" in res.seen_files


def test_deterministic_priority_ordering_across_permutations(workspace):
    """Permuting the order of intent clauses in user request produces identical, deterministic output."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.det">\n'
        '  <application android:label="@string/app_name">\n'
        '    <activity android:name="com.det.MainActivity">\n'
        "      <intent-filter>\n"
        '        <action android:name="android.intent.action.MAIN"/>\n'
        '        <category android:name="android.intent.category.LAUNCHER"/>\n'
        "      </intent-filter>\n"
        "    </activity>\n"
        "  </application>\n"
        "</manifest>"
    )
    val_dir = decoded / "res/values"
    val_dir.mkdir(parents=True, exist_ok=True)
    (val_dir / "strings.xml").write_text(
        "<resources><string name='app_name'>Det</string></resources>"
    )
    smali_dir = decoded / "smali/com/det"
    smali_dir.mkdir(parents=True, exist_ok=True)
    (smali_dir / "MainActivity.smali").write_text(".class public Lcom/det/MainActivity;")
    (smali_dir / "HttpClient.smali").write_text(".class public Lcom/det/HttpClient;")

    tools = AiContextTools(workspace)
    analysis = AnalysisResult(project_id="test_det", package_name="com.det", runtimes={"dalvik"})
    router = IntentRouter()

    prompts = [
        "rename app, show toast on click, ping server on launch",
        "ping server on launch, rename app, show toast on click",
        "show toast on click, ping server on launch, rename app",
        "rename app, ping server on launch, show toast on click",
        "ping server on launch, show toast on click, rename app",
        "show toast on click, rename app, ping server on launch",
    ]

    results = [router.route(p, tools, analysis) for p in prompts]
    base_intents = results[0].matched_intents
    base_file_order = list(results[0].seen_files.keys())

    for i, res in enumerate(results[1:], start=1):
        assert res.matched_intents == base_intents, (
            f"Permutation {i} matched intents order deviated: {res.matched_intents}"
        )
        assert list(res.seen_files.keys()) == base_file_order, (
            f"Permutation {i} seen_files order deviated: {list(res.seen_files.keys())}"
        )


def test_boundary_cap_max_8_files_per_intent(workspace):
    """An intent with many candidate files strictly respects the 8-file per-intent cap."""
    decoded = workspace.decoded_dir
    lay_dir = decoded / "res/layout"
    lay_dir.mkdir(parents=True, exist_ok=True)
    for i in range(25):
        (lay_dir / f"layout_{i:02d}.xml").write_text(f"<View id='{i}'/>")

    tools = AiContextTools(workspace)
    analysis = AnalysisResult(project_id="test_cap8", runtimes={"dalvik"})
    router = IntentRouter()

    res = router.route("modify screen layout", tools, analysis)
    assert "ui_layout" in res.matched_intents
    assert len(res.seen_files) == 8


def test_boundary_cap_max_20_files_total(workspace):
    """Multiple broad intents with 50+ total candidates strictly cap at 20 files total."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.cap20">\n'
        '  <application android:label="@string/app_name">\n'
        '    <activity android:name="com.cap20.MainActivity">\n'
        "      <intent-filter>\n"
        '        <action android:name="android.intent.action.MAIN"/>\n'
        '        <category android:name="android.intent.category.LAUNCHER"/>\n'
        "      </intent-filter>\n"
        "    </activity>\n"
        "  </application>\n"
        "</manifest>"
    )
    val_dir = decoded / "res/values"
    val_dir.mkdir(parents=True, exist_ok=True)
    (val_dir / "strings.xml").write_text(
        "<resources><string name='app_name'>App</string></resources>"
    )
    for i in range(10):
        d = decoded / f"res/values-l{i:02d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "strings.xml").write_text("<resources/>")

    lay_dir = decoded / "res/layout"
    lay_dir.mkdir(parents=True, exist_ok=True)
    for i in range(15):
        (lay_dir / f"lay_{i:02d}.xml").write_text("<View/>")

    smali_dir = decoded / "smali/com/cap20"
    smali_dir.mkdir(parents=True, exist_ok=True)
    (smali_dir / "MainActivity.smali").write_text(".class public Lcom/cap20/MainActivity;")
    for i in range(1, 10):
        (smali_dir / f"MainActivity${i}.smali").write_text(
            f".class public Lcom/cap20/MainActivity${i};"
        )

    for i in range(10):
        (smali_dir / f"NetworkClient{i:02d}.smali").write_text(
            f".class public Lcom/cap20/NetworkClient{i:02d};"
        )

    for i in range(10):
        (smali_dir / f"SyncService{i:02d}.smali").write_text(
            f".class public Lcom/cap20/SyncService{i:02d};"
        )

    tools = AiContextTools(workspace)
    analysis = AnalysisResult(
        project_id="test_cap20", package_name="com.cap20", runtimes={"dalvik"}
    )
    router = IntentRouter()

    req = (
        "rename app, show toast on click, ping server on launch, "
        "change button text in layout, and modify background service"
    )
    res = router.route(req, tools, analysis)

    assert len(res.seen_files) == 20
    assert len(res.seen_files) == len(set(res.seen_files.keys()))


def test_deduplication_zero_duplicates_across_overlapping_intents(workspace):
    """Candidate files common to multiple matched intents appear exactly once in seen_files."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.dedup">\n'
        '  <application android:label="@string/app_name">\n'
        '    <activity android:name="com.dedup.MainActivity">\n'
        "      <intent-filter>\n"
        '        <action android:name="android.intent.action.MAIN"/>\n'
        '        <category android:name="android.intent.category.LAUNCHER"/>\n'
        "      </intent-filter>\n"
        "    </activity>\n"
        '    <receiver android:name="com.dedup.AlarmReceiver"/>\n'
        "  </application>\n"
        "</manifest>"
    )
    val_dir = decoded / "res/values"
    val_dir.mkdir(parents=True, exist_ok=True)
    (val_dir / "strings.xml").write_text(
        "<resources><string name='app_name'>App</string></resources>"
    )

    lay_dir = decoded / "res/layout"
    lay_dir.mkdir(parents=True, exist_ok=True)
    (lay_dir / "activity_main.xml").write_text("<LinearLayout/>")

    smali_dir = decoded / "smali/com/dedup"
    smali_dir.mkdir(parents=True, exist_ok=True)
    (smali_dir / "MainActivity.smali").write_text(".class public Lcom/dedup/MainActivity;")
    (smali_dir / "AlarmReceiver.smali").write_text(".class public Lcom/dedup/AlarmReceiver;")

    tools = AiContextTools(workspace)
    analysis = AnalysisResult(
        project_id="test_dedup", package_name="com.dedup", runtimes={"dalvik"}
    )
    router = IntentRouter()

    req = "rename app, show toast on click, change layout button color, grant camera permission, and add receiver"
    res = router.route(req, tools, analysis)

    file_keys = list(res.seen_files.keys())
    assert len(file_keys) == len(set(file_keys)), "Duplicate file detected in seen_files!"
    assert file_keys.count("AndroidManifest.xml") == 1
    assert file_keys.count("res/values/strings.xml") == 1


def test_empty_and_sparse_workspace_resilience(workspace):
    """Empty and sparse workspaces do not raise exceptions and return valid route results."""
    tools = AiContextTools(workspace)
    analysis = AnalysisResult(project_id="test_sparse", runtimes={"dalvik"})
    router = IntentRouter()

    # 1. 0-file workspace
    res_empty = router.route("rename app and show toast on click", tools, analysis)
    assert set(res_empty.matched_intents) == {"app_name", "toast_flash"}
    assert res_empty.seen_files == {}
    assert res_empty.stop_reason == "intent_matched"

    # 2. Sparse workspace with only AndroidManifest.xml
    (workspace.decoded_dir / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.sparse"/>'
    )
    tools._indexed_paths = None
    res_sparse = router.route("grant camera permission and rename app", tools, analysis)
    assert "permission" in res_sparse.matched_intents
    assert "app_name" in res_sparse.matched_intents
    assert list(res_sparse.seen_files.keys()) == ["AndroidManifest.xml"]


def test_massive_workspace_scaling_and_throughput(workspace):
    """Workspace with 600+ files executes under 0.5s for 7 concurrent intents."""
    import time

    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.massive">\n'
        '  <application android:label="@string/app_name">\n'
        '    <activity android:name="com.massive.MainActivity">\n'
        "      <intent-filter>\n"
        '        <action android:name="android.intent.action.MAIN"/>\n'
        '        <category android:name="android.intent.category.LAUNCHER"/>\n'
        "      </intent-filter>\n"
        "    </activity>\n"
        '    <receiver android:name="com.massive.Receiver000"/>\n'
        "  </application>\n"
        "</manifest>"
    )
    all_paths = ["AndroidManifest.xml"]

    # 100 strings files
    for i in range(100):
        p = f"res/values-l{i:03d}/strings.xml"
        all_paths.append(p)
        (decoded / f"res/values-l{i:03d}").mkdir(parents=True, exist_ok=True)
        (decoded / p).write_text("<resources/>")

    # 100 layout files
    (decoded / "res/layout").mkdir(parents=True, exist_ok=True)
    for i in range(100):
        p = f"res/layout/lay_{i:03d}.xml"
        all_paths.append(p)
        (decoded / p).write_text("<View/>")

    # 100 launcher smali inner classes
    (decoded / "smali/com/massive").mkdir(parents=True, exist_ok=True)
    all_paths.append("smali/com/massive/MainActivity.smali")
    (decoded / "smali/com/massive/MainActivity.smali").write_text(
        ".class public Lcom/massive/MainActivity;"
    )
    for i in range(1, 101):
        p = f"smali/com/massive/MainActivity${i}.smali"
        all_paths.append(p)
        (decoded / p).write_text(f".class public Lcom/massive/MainActivity${i};")

    # 100 network smali files
    for i in range(100):
        p = f"smali/com/massive/HttpClient{i:03d}.smali"
        all_paths.append(p)
        (decoded / p).write_text(f".class public Lcom/massive/HttpClient{i:03d};")

    # 100 receiver smali files
    for i in range(100):
        p = f"smali/com/massive/Receiver{i:03d}.smali"
        all_paths.append(p)
        (decoded / p).write_text(f".class public Lcom/massive/Receiver{i:03d};")

    # 100 native libs
    (decoded / "lib/arm64-v8a").mkdir(parents=True, exist_ok=True)
    for i in range(100):
        p = f"lib/arm64-v8a/libmod{i:03d}.so"
        all_paths.append(p)
        (decoded / p).write_bytes(b"ELF")

    tools = AiContextTools(workspace)
    tools._indexed_paths = all_paths
    analysis = AnalysisResult(
        project_id="massive",
        package_name="com.massive",
        runtimes={"dalvik", "native"},
        components=[
            ComponentInfo(
                name="com.massive.MainActivity", component_type="activity", is_launcher=True
            )
        ],
    )
    router = IntentRouter()

    req = (
        "rename app, show toast on click, ping server on launch, "
        "change button text in layout, grant permission, add broadcast receiver, "
        "and patch native jni library"
    )

    t0 = time.perf_counter()
    res = router.route(req, tools, analysis)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.5, f"Execution took too long: {elapsed:.3f}s"
    assert len(res.seen_files) == 20
    assert len(res.seen_files) == len(set(res.seen_files.keys()))
    assert len(res.matched_intents) == 7


def test_round_robin_merge_does_not_starve_candidates_on_duplicate_rounds(workspace):
    """Round-robin merge must not terminate prematurely when an intermediate round encounters only duplicate candidates."""
    decoded = workspace.decoded_dir
    (decoded / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.starve">\n'
        '  <application android:label="@string/app_name">\n'
        '    <activity android:name="com.starve.MainActivity">\n'
        "      <intent-filter>\n"
        '        <action android:name="android.intent.action.MAIN"/>\n'
        '        <category android:name="android.intent.category.LAUNCHER"/>\n'
        "      </intent-filter>\n"
        "    </activity>\n"
        "  </application>\n"
        "</manifest>"
    )
    # strings: default + 2 translations (app_name candidates: manifest + strings + en + es = 4 files)
    val_dir = decoded / "res/values"
    val_dir.mkdir(parents=True, exist_ok=True)
    (val_dir / "strings.xml").write_text(
        "<resources><string name='app_name'>Starve</string></resources>"
    )
    val_en = decoded / "res/values-en"
    val_en.mkdir(parents=True, exist_ok=True)
    (val_en / "strings.xml").write_text("<resources/>")
    val_es = decoded / "res/values-es"
    val_es.mkdir(parents=True, exist_ok=True)
    (val_es / "strings.xml").write_text("<resources/>")

    # 7 layout files (ui_layout candidates: layout_1..4, strings.xml, layout_5..7)
    lay_dir = decoded / "res/layout"
    lay_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, 8):
        (lay_dir / f"layout_{i}.xml").write_text(f"<View id='{i}'/>")

    tools = AiContextTools(workspace)
    analysis = AnalysisResult(
        project_id="test_starve", package_name="com.starve", runtimes={"dalvik"}
    )
    router = IntentRouter()

    # Matches: app_name, ui_layout, permission
    # In round 4: permission exhausted, app_name exhausted, ui_layout has strings.xml (already added in round 1)
    # Bug causes loop to break, dropping layout_5.xml, layout_6.xml, layout_7.xml!
    req = "rename app, grant camera permission, and change button text in layout xml"
    res = router.route(req, tools, analysis)

    assert set(res.matched_intents) == {"app_name", "ui_layout", "permission"}
    # layout_5.xml, layout_6.xml, layout_7.xml MUST NOT be starved!
    assert "res/layout/layout_5.xml" in res.seen_files, (
        "layout_5.xml was starved by duplicate round termination bug"
    )
    assert "res/layout/layout_6.xml" in res.seen_files, (
        "layout_6.xml was starved by duplicate round termination bug"
    )
    assert "res/layout/layout_7.xml" in res.seen_files, (
        "layout_7.xml was starved by duplicate round termination bug"
    )
