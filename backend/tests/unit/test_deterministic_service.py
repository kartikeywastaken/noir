import zipfile
from types import SimpleNamespace

from noir.application.deterministic_service import (
    ANDROID,
    OperationSpec,
    _configured_manifest,
    _existing_runtime_dex,
    _next_dex_name,
    _runtime_dex,
    parse_operation_spec,
)
from noir.patches.smali_validator import SmaliBytecodeValidator
from noir.security.xml import parse


def test_parse_all_guaranteed_operations_without_ai():
    spec = parse_operation_spec(
        "Rename the app to NOIR Demo, open https://example.com when the app launches "
        "and show a toast message saying 'Ready' on every tap"
    )

    assert spec is not None
    assert spec.app_name == "NOIR Demo"
    assert spec.launch_url == "https://example.com"
    assert spec.interaction_toast == "Ready"
    assert spec.intents == ["app_name", "launch_redirect", "interaction_toast"]
    assert spec.strategies == ["existing_file", "injected_dex", "manifest_component"]


def test_advanced_clause_does_not_enter_deterministic_profile():
    assert (
        parse_operation_spec("Rename the app to Demo and add camera permission")
        is None
    )


def test_http_request_on_launch_is_not_misclassified_as_browser_redirect():
    assert (
        parse_operation_spec("Send an HTTP request to https://example.com on app launch")
        is None
    )


def test_enclosing_method_duplicate_label_is_rejected():
    existing = """.class public Lx/Test;
.super Ljava/lang/Object;
.method public run()V
    .locals 1
    :cond_0
    return-void
.end method
"""

    result = SmaliBytecodeValidator.validate(
        ":cond_0\nreturn-void",
        context_method="run()V",
        context_class="Lx/Test;",
        enclosing_file_content=existing,
    )

    assert not result.is_valid
    assert any(item.error_code == "DUPLICATE_LABEL" for item in result.diagnostics)


def test_redirect_proxies_every_launcher_without_app_specific_classes(tmp_path):
    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="example.app">
  <application android:label="@string/app_name">
    <activity android:name=".ObfuscatedA"><intent-filter>
      <action android:name="android.intent.action.MAIN"/>
      <category android:name="android.intent.category.LAUNCHER"/>
    </intent-filter></activity>
    <activity android:name="example.app.Second"><intent-filter>
      <action android:name="android.intent.action.MAIN"/>
      <category android:name="android.intent.category.LAUNCHER"/>
    </intent-filter></activity>
  </application>
</manifest>""",
        encoding="utf-8",
    )

    configured = _configured_manifest(
        manifest,
        OperationSpec(launch_url="https://example.com"),
        "example.app.noir.runtime.v1",
    )
    manifest.write_text(configured, encoding="utf-8")
    root = parse(manifest).getroot()
    app = root.find("application")
    aliases = [
        item
        for item in app.findall("activity-alias")
        if item.get(ANDROID + "name", "").startswith("in.v0id.noir.injected.")
    ]

    assert len(aliases) == 2
    assert all(alias.findall("intent-filter") for alias in aliases)
    assert not any(activity.findall("intent-filter") for activity in app.findall("activity")[:-1])


def test_next_dex_uses_highest_index_even_when_numbers_have_gaps(tmp_path):
    input_dir = tmp_path / "input"
    decoded_dir = tmp_path / "decoded"
    input_dir.mkdir()
    decoded_dir.mkdir()
    with zipfile.ZipFile(input_dir / "fixture.apk", "w") as archive:
        archive.writestr("classes.dex", b"one")
        archive.writestr("classes4.dex", b"four")

    workspace = SimpleNamespace(input_dir=input_dir, decoded_dir=decoded_dir)
    assert _next_dex_name(workspace) == "classes5.dex"


def test_existing_runtime_dex_is_reused_instead_of_duplicated(tmp_path):
    input_dir = tmp_path / "input"
    decoded_dir = tmp_path / "decoded"
    input_dir.mkdir()
    decoded_dir.mkdir()
    runtime = _runtime_dex()
    with zipfile.ZipFile(input_dir / "fixture.apk", "w") as archive:
        archive.writestr("classes.dex", b"original")
        archive.writestr("classes2.dex", runtime)

    workspace = SimpleNamespace(input_dir=input_dir, decoded_dir=decoded_dir)
    assert _existing_runtime_dex(workspace, runtime) == "classes2.dex"
