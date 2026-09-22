import zipfile
from types import SimpleNamespace

from noir.application.deterministic_service import (
    ANDROID,
    OperationSpec,
    _configured_manifest,
    _existing_runtime_dex,
    _next_dex_name,
    _runtime_dex,
    create_deterministic_patch,
    create_deterministic_plan,
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
    assert parse_operation_spec("Rename the app to Demo and add camera permission") is None


def test_http_request_on_launch_is_not_misclassified_as_browser_redirect():
    assert parse_operation_spec("Send an HTTP request to https://example.com on app launch") is None


def test_internet_permission_uses_deterministic_manifest_path():
    spec = parse_operation_spec(
        "Add android.permission.INTERNET permission to AndroidManifest.xml.",
        {"permission"},
    )

    assert spec is not None
    assert spec.permission == "android.permission.INTERNET"
    assert spec.intents == ["permission"]
    assert spec.strategies == ["existing_file"]


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


def test_multidex_plan_patch_and_store_use_same_concrete_binary_path(tmp_path):
    from noir.application.patch_service import PatchService, PlanService
    from noir.domain.config import NoirConfig
    from noir.domain.enums import PatchOperationType
    from noir.domain.models import ProjectInfo
    from noir.infrastructure.database.engine import init_db
    from noir.infrastructure.database.repositories import ProjectRepository
    from noir.infrastructure.filesystem.workspace import ProjectWorkspace

    config = NoirConfig(_env_file=None, data_dir=str(tmp_path / "data"), ai_provider="none")
    config.ensure_directories()
    init_db(config.effective_database_url)
    project = ProjectInfo(package_name="example.app")
    ProjectRepository().create(project)
    workspace = ProjectWorkspace(project.id, config)
    workspace.create()
    with zipfile.ZipFile(workspace.input_dir / "fixture.apk", "w") as archive:
        archive.writestr("classes.dex", b"one")
        archive.writestr("classes4.dex", b"four")
    (workspace.decoded_dir / "AndroidManifest.xml").write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="example.app">
  <application android:label="Example">
    <activity android:name=".MainActivity" android:exported="true">
      <intent-filter>
        <action android:name="android.intent.action.MAIN"/>
        <category android:name="android.intent.category.LAUNCHER"/>
      </intent-filter>
    </activity>
  </application>
</manifest>""",
        encoding="utf-8",
    )
    spec = OperationSpec(launch_url="https://example.com")

    plan = create_deterministic_plan(
        project.id, project.workspace_revision, "open https://example.com", spec, workspace
    )
    assert ("classes5.dex", PatchOperationType.CREATE_FILE) in {
        (change.relative_path, change.operation) for change in plan.file_changes
    }
    assert all("<" not in change.relative_path for change in plan.file_changes)
    plan = PlanService(config).create_plan(plan)
    patch = create_deterministic_patch(workspace, plan, spec, "unused")

    stored = PatchService(config).store_patch(patch, preview=True)

    assert stored.patch_id == patch.patch_id
    assert ("classes5.dex", PatchOperationType.CREATE_FILE) in {
        (operation.relative_path, operation.operation) for operation in stored.operations
    }


def test_permission_plan_patch_and_store_use_safe_manifest_replacement(tmp_path):
    from noir.application.patch_service import PatchService, PlanService
    from noir.domain.config import NoirConfig
    from noir.domain.enums import PatchOperationType
    from noir.domain.models import ProjectInfo
    from noir.infrastructure.database.engine import init_db
    from noir.infrastructure.database.repositories import ProjectRepository
    from noir.infrastructure.filesystem.workspace import ProjectWorkspace

    config = NoirConfig(_env_file=None, data_dir=str(tmp_path / "data"), ai_provider="none")
    config.ensure_directories()
    init_db(config.effective_database_url)
    project = ProjectInfo(package_name="example.permission")
    ProjectRepository().create(project)
    workspace = ProjectWorkspace(project.id, config)
    workspace.create()
    manifest = workspace.decoded_dir / "AndroidManifest.xml"
    manifest.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
          package="example.permission">
  <application android:label="Example"/>
</manifest>""",
        encoding="utf-8",
    )
    spec = OperationSpec(permission="android.permission.INTERNET")

    plan = create_deterministic_plan(
        project.id, 0, "Add android.permission.INTERNET permission", spec, workspace
    )
    assert [(item.relative_path, item.operation) for item in plan.file_changes] == [
        ("AndroidManifest.xml", PatchOperationType.REPLACE_FILE)
    ]
    plan = PlanService(config).create_plan(plan)
    patch = create_deterministic_patch(workspace, plan, spec, "unused")

    stored = PatchService(config).store_patch(patch, preview=True)

    assert len(stored.operations) == 1
    assert "android.permission.INTERNET" in (stored.operations[0].new_content or "")


def test_existing_permission_returns_no_change_plan(tmp_path):
    input_dir = tmp_path / "input"
    decoded_dir = tmp_path / "decoded"
    input_dir.mkdir()
    decoded_dir.mkdir()
    (decoded_dir / "AndroidManifest.xml").write_text(
        """<manifest xmlns:android="http://schemas.android.com/apk/res/android"
package="example.permission">
<uses-permission android:name="android.permission.INTERNET"/>
<application/>
</manifest>""",
        encoding="utf-8",
    )
    workspace = SimpleNamespace(input_dir=input_dir, decoded_dir=decoded_dir)
    spec = OperationSpec(permission="android.permission.INTERNET")

    plan = create_deterministic_plan("permission-test", 0, "add internet", spec, workspace)

    assert plan.file_changes == []
    assert plan.permission_changes == []
    assert plan.unsupported_aspects == [
        "android.permission.INTERNET is already declared; no change is required"
    ]
