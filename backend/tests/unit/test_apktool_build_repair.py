"""Comprehensive unit tests for Apple Metadata Defense and Compiler-Feedback Build Repair Loop."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from noir.application.build_service import (
    BuildRepairExhaustedError,
    BuildService,
    BuildServiceError,
)
from noir.domain.config import NoirConfig, reset_config
from noir.domain.models import ProcessResult, ProjectInfo
from noir.infrastructure.database.engine import init_db
from noir.infrastructure.database.repositories import (
    EventRepository,
    FileManifestRepository,
    ProjectRepository,
)
from noir.infrastructure.filesystem.workspace import ProjectWorkspace
from noir.infrastructure.tools.apktool import (
    ApkToolAdapter,
    ApkToolError,
    sanitize_host_metadata,
)
from noir.infrastructure.tools.diagnostic_parser import CompilerDiagnostic


@pytest.fixture
def test_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("NOIR_DATA_DIR", str(data_dir))
    reset_config()
    from noir.infrastructure.database.engine import get_engine

    existing_engine = get_engine()
    if existing_engine is not None:
        existing_engine.dispose()
    cfg = NoirConfig(_env_file=None, gemini_api_key="", data_dir=str(data_dir))
    cfg.ensure_directories()
    init_db(cfg.effective_database_url)

    project = ProjectInfo(package_name="com.noir.testbuild")
    ProjectRepository().create(project)
    ws = ProjectWorkspace(project.id, cfg)
    ws.create()

    # Create baseline valid manifest and files
    (ws.decoded_dir / "AndroidManifest.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
        'package="com.noir.testbuild">\n'
        '    <application android:label="TestApp">\n'
        '        <activity android:name=".MainActivity" />\n'
        "    </application>\n"
        "</manifest>\n",
        encoding="utf-8",
    )
    (ws.decoded_dir / "res" / "values").mkdir(parents=True, exist_ok=True)
    (ws.decoded_dir / "res" / "values" / "strings.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
        '    <string name="app_name">TestApp</string>\n</resources>\n',
        encoding="utf-8",
    )

    FileManifestRepository().save(project.id, 0, ws.build_file_manifest())
    try:
        yield cfg, ws
    finally:
        from noir.infrastructure.database.engine import get_engine

        engine = get_engine()
        if engine is not None:
            engine.dispose()
        reset_config()


# ============================================================================
# 1. Apple Metadata Defense Tests (sanitize_host_metadata)
# ============================================================================


def test_sanitize_host_metadata_deletes_ds_store_and_appledouble(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "decoded_apk"
    workspace_dir.mkdir()
    res_dir = workspace_dir / "res" / "values"
    res_dir.mkdir(parents=True)

    # Legitimate files
    manifest = workspace_dir / "AndroidManifest.xml"
    manifest.write_text("<manifest></manifest>")
    strings = res_dir / "strings.xml"
    strings.write_text("<resources></resources>")

    # Pollution files
    root_ds_store = workspace_dir / ".DS_Store"
    root_ds_store.write_bytes(b"\x00\x00\x00\x01Bud1")
    nested_ds_store = res_dir / ".DS_Store"
    nested_ds_store.write_bytes(b"\x00\x00\x00\x01Bud1")

    root_appledouble = workspace_dir / "._AndroidManifest.xml"
    root_appledouble.write_bytes(b"\x00\x05\x16\x07AppleDoubleHeader")
    nested_appledouble = res_dir / "._strings.xml"
    nested_appledouble.write_bytes(b"\x00\x05\x16\x07AppleDoubleHeader")

    stats = sanitize_host_metadata(workspace_dir)

    # Assert pollution is deleted
    assert not root_ds_store.exists()
    assert not nested_ds_store.exists()
    assert not root_appledouble.exists()
    assert not nested_appledouble.exists()

    # Assert legitimate files are untouched
    assert manifest.exists()
    assert strings.exists()

    assert stats["ds_store"] == 2
    assert stats["apple_double"] == 2


def test_sanitize_host_metadata_strips_extended_attributes(tmp_path: Path) -> None:
    sample_file = tmp_path / "resource.xml"
    sample_file.write_text("<xml />")

    if hasattr(os, "setxattr") and hasattr(os, "listxattr"):
        try:
            os.setxattr(sample_file, "com.apple.provenance", b"test_provenance")
            os.setxattr(sample_file, "com.apple.quarantine", b"test_quarantine")
            assert "com.apple.provenance" in os.listxattr(sample_file)

            stats = sanitize_host_metadata(tmp_path)
            assert "com.apple.provenance" not in os.listxattr(sample_file)
            assert stats["xattrs"] >= 2
        except OSError:
            # Filesystem might not support xattrs in temporary sandbox
            pass


def test_sanitize_host_metadata_skips_symlinks(tmp_path: Path) -> None:
    target_dir = tmp_path / "real_dir"
    target_dir.mkdir()
    real_file = target_dir / "target.txt"
    real_file.write_text("keep_me")

    symlink_file = tmp_path / "._symlink_apple"
    try:
        symlink_file.symlink_to(real_file)
    except OSError:
        pytest.skip("Symlink creation not supported")

    # Symlinks starting with ._ should not delete the underlying target
    sanitize_host_metadata(tmp_path)
    assert real_file.exists()


def test_apktool_adapter_build_invokes_sanitize_host_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = NoirConfig(_env_file=None, gemini_api_key="", data_dir=str(tmp_path))
    adapter = ApkToolAdapter(cfg)

    decoded_dir = tmp_path / "decoded"
    decoded_dir.mkdir()
    (decoded_dir / ".DS_Store").write_bytes(b"bad")
    (decoded_dir / "._manifest.xml").write_bytes(b"bad")

    output_apk = tmp_path / "out.apk"

    def mock_run_tool(cmd, **_kwargs):
        # Create dummy output so build succeeds check
        output_apk.write_bytes(b"PK\x03\x04")
        return ProcessResult(command=cmd, exit_code=0, tool_name="apktool")

    monkeypatch.setattr("noir.infrastructure.tools.apktool.run_tool", mock_run_tool)
    monkeypatch.setattr(adapter, "_check_version", lambda: None)

    adapter.build(decoded_dir, output_apk)

    # Verify sanitize was invoked before build finished
    assert not (decoded_dir / ".DS_Store").exists()
    assert not (decoded_dir / "._manifest.xml").exists()


# ============================================================================
# 2. Targeted Repair Handler Unit Tests
# ============================================================================


def test_repair_missing_return_descriptor(test_workspace) -> None:
    cfg, ws = test_workspace
    service = BuildService(cfg)

    smali_dir = ws.decoded_dir / "smali" / "com" / "example"
    smali_dir.mkdir(parents=True)
    smali_file = smali_dir / "MainActivity.smali"
    broken_smali = (
        ".class public Lcom/example/MainActivity;\n"
        ".super Landroid/app/Activity;\n"
        ".method public onCreate()V\n"
        "    invoke-virtual {v0}, Landroid/widget/Toast;->show()\n"
        "    return-void\n"
        ".end method\n"
    )
    smali_file.write_text(broken_smali, encoding="utf-8")

    diag = CompilerDiagnostic(
        file_path="smali/com/example/MainActivity.smali",
        line_number=4,
        message="missing return descriptor for invoke-virtual {v0}, Landroid/widget/Toast;->show()",
        failure_shape="missing_return_descriptor",
    )

    repaired = service.repair_failure(
        build_workspace=ws.decoded_dir,
        failure_shape="missing_return_descriptor",
        primary_diagnostic=diag,
    )
    assert repaired is True
    content = smali_file.read_text(encoding="utf-8")
    assert "->show()V" in content


def test_repair_duplicate_attribute(test_workspace) -> None:
    cfg, ws = test_workspace
    service = BuildService(cfg)

    manifest_file = ws.decoded_dir / "AndroidManifest.xml"
    broken_manifest = (
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
        'package="com.noir.test">\n'
        '    <application android:name=".App" android:name=".App">\n'
        '        <activity android:name=".MainActivity" />\n'
        "    </application>\n"
        "</manifest>\n"
    )
    manifest_file.write_text(broken_manifest, encoding="utf-8")

    diag = CompilerDiagnostic(
        file_path="AndroidManifest.xml",
        line_number=2,
        message="duplicate attribute 'android:name'",
        failure_shape="duplicate_attribute",
    )

    repaired = service.repair_failure(
        build_workspace=ws.decoded_dir,
        failure_shape="duplicate_attribute",
        primary_diagnostic=diag,
    )
    assert repaired is True
    content = manifest_file.read_text(encoding="utf-8")
    # Verify android:name appears only once in application tag
    app_line = next(line for line in content.splitlines() if "<application" in line)
    assert app_line.count('android:name="') == 1


def test_repair_missing_resource_attr(test_workspace) -> None:
    cfg, ws = test_workspace
    service = BuildService(cfg)

    manifest_file = ws.decoded_dir / "AndroidManifest.xml"
    broken_manifest = (
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
        'package="com.noir.test">\n'
        "    <application>\n"
        '        <activity android:exported="true" />\n'
        "    </application>\n"
        "</manifest>\n"
    )
    manifest_file.write_text(broken_manifest, encoding="utf-8")

    diag = CompilerDiagnostic(
        file_path="AndroidManifest.xml",
        line_number=3,
        message='element <activity> is missing "android:name" attribute',
        failure_shape="missing_resource_attr",
    )

    repaired = service.repair_failure(
        build_workspace=ws.decoded_dir,
        failure_shape="missing_resource_attr",
        primary_diagnostic=diag,
    )
    assert repaired is True
    content = manifest_file.read_text(encoding="utf-8")
    assert 'android:name="' in content


def test_repair_duplicate_resource(test_workspace) -> None:
    cfg, ws = test_workspace
    service = BuildService(cfg)

    strings_file = ws.decoded_dir / "res" / "values" / "strings.xml"
    broken_strings = (
        "<resources>\n"
        '    <string name="app_name">Original</string>\n'
        '    <string name="app_name">Duplicate</string>\n'
        "</resources>\n"
    )
    strings_file.write_text(broken_strings, encoding="utf-8")

    diag = CompilerDiagnostic(
        file_path="res/values/strings.xml",
        line_number=3,
        message="duplicate value for resource 'string/app_name'",
        failure_shape="duplicate_resource",
    )

    repaired = service.repair_failure(
        build_workspace=ws.decoded_dir,
        failure_shape="duplicate_resource",
        primary_diagnostic=diag,
    )
    assert repaired is True
    content = strings_file.read_text(encoding="utf-8")
    assert content.count('name="app_name"') == 1
    assert "Original" in content


def test_repair_undefined_resource(test_workspace) -> None:
    cfg, ws = test_workspace
    service = BuildService(cfg)

    strings_file = ws.decoded_dir / "res" / "values" / "strings.xml"
    diag = CompilerDiagnostic(
        file_path="res/layout/activity_main.xml",
        line_number=5,
        message="resource string/custom_label not found.",
        failure_shape="undefined_resource",
    )

    repaired = service.repair_failure(
        build_workspace=ws.decoded_dir,
        failure_shape="undefined_resource",
        primary_diagnostic=diag,
    )
    assert repaired is True
    content = strings_file.read_text(encoding="utf-8")
    assert '<string name="custom_label">custom_label</string>' in content


# ============================================================================
# 3. Two-Strike Rule & Bounded Self-Repair Loop Tests
# ============================================================================


def test_build_repair_loop_succeeds_on_first_repair(
    test_workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ws = test_workspace
    service = BuildService(cfg)

    # Plant missing return descriptor in smali
    smali_dir = ws.decoded_dir / "smali"
    smali_dir.mkdir(parents=True, exist_ok=True)
    smali_file = smali_dir / "ToastHelper.smali"
    smali_file.write_text("invoke-virtual {v0}, Landroid/widget/Toast;->show()\n", encoding="utf-8")
    FileManifestRepository().save(ws.project_id, 1, ws.build_file_manifest())
    project = ProjectRepository().get(ws.project_id)
    assert project is not None
    project.workspace_revision = 1
    ProjectRepository().update(project)

    call_count = 0

    def fake_build(build_workspace, output_apk, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            err = ProcessResult(
                command=["apktool", "b"],
                exit_code=1,
                stderr=(
                    "smali/ToastHelper.smali:1: error: missing return descriptor for "
                    "invoke-virtual {v0}, Landroid/widget/Toast;->show()"
                ),
                tool_name="apktool",
            )
            raise ApkToolError("Build failed", err)

        # On second attempt (after repair), succeed:
        with zipfile.ZipFile(output_apk, "w") as archive:
            archive.writestr("AndroidManifest.xml", "manifest")
        return SimpleNamespace(tool_version="3.0.0", duration_seconds=0.1, stdout="ok", stderr="")

    monkeypatch.setattr(service.apktool, "build", fake_build)

    result = service.build(ws.project_id)
    assert result.success is True
    assert result.attempt_number == 2
    assert call_count == 2


def test_two_strike_rule_stops_after_two_attempts_of_same_failure_shape(
    test_workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ws = test_workspace
    service = BuildService(cfg)

    call_count = 0

    def repeatedly_failing_build(build_workspace, output_apk, **_kwargs):
        nonlocal call_count
        call_count += 1
        err = ProcessResult(
            command=["apktool", "b"],
            exit_code=1,
            stderr=(
                "AndroidManifest.xml:14:5: AAPT: error: element <activity> is "
                'missing "android:name" attribute.'
            ),
            tool_name="apktool",
        )
        raise ApkToolError("Repeated build failure", err)

    monkeypatch.setattr(service.apktool, "build", repeatedly_failing_build)

    with pytest.raises(BuildRepairExhaustedError) as exc_info:
        service.build(ws.project_id)

    assert exc_info.value.failure_shape == "missing_resource_attr"
    assert exc_info.value.strikes == 3
    assert "Two-Strike Rule triggered" in str(exc_info.value)
    # 1 initial build attempt + 2 repair retry attempts = 3 calls total
    assert call_count == 3

    # Check audit events
    events = EventRepository().list_by_project(ws.project_id)
    exhaust_event = next((e for e in events if "Two-Strike Rule triggered" in e.message), None)
    assert exhaust_event is not None
    assert exhaust_event.metadata["failure_shape"] == "missing_resource_attr"
    assert exhaust_event.metadata["strike_count"] == 3


def test_two_strike_rule_independent_per_failure_shape(
    test_workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ws = test_workspace
    service = BuildService(cfg)

    call_count = 0

    def mixed_failures_then_success(build_workspace, output_apk, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Shape 1: missing_return_descriptor (strike 1 for shape 1)
            err = ProcessResult(
                command=["apktool", "b"],
                exit_code=1,
                stderr="smali/Test.smali:10: error: missing return descriptor for invoke-virtual",
                tool_name="apktool",
            )
            raise ApkToolError("Smali error", err)
        if call_count == 2:
            # Shape 2: duplicate_attribute (strike 1 for shape 2)
            err = ProcessResult(
                command=["apktool", "b"],
                exit_code=1,
                stderr="AndroidManifest.xml:12: error: duplicate attribute 'android:exported'",
                tool_name="apktool",
            )
            raise ApkToolError("XML error", err)

        # Attempt 3: succeed
        with zipfile.ZipFile(output_apk, "w") as archive:
            archive.writestr("AndroidManifest.xml", "manifest")
        return SimpleNamespace(tool_version="3.0.0", duration_seconds=0.1, stdout="ok", stderr="")

    monkeypatch.setattr(service.apktool, "build", mixed_failures_then_success)

    result = service.build(ws.project_id)
    assert result.success is True
    assert result.attempt_number == 3
    assert call_count == 3


def test_build_repair_aborts_immediately_on_unrepairable_shape(
    test_workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ws = test_workspace
    service = BuildService(cfg)

    call_count = 0

    def unrepairable_failure(build_workspace, output_apk, **_kwargs):
        nonlocal call_count
        call_count += 1
        err = ProcessResult(
            command=["apktool", "b"],
            exit_code=1,
            stderr="Fatal unknown unrecoverable system crash 0xdeadbeef",
            tool_name="apktool",
        )
        raise ApkToolError("Unrepairable", err)

    monkeypatch.setattr(service.apktool, "build", unrepairable_failure)

    with pytest.raises(BuildServiceError):
        service.build(ws.project_id)

    # Should not retry since failure shape cannot be repaired
    assert call_count == 1
