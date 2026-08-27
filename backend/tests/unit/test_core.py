"""Unit tests for NOIR domain models, config, validation, and core logic."""

from __future__ import annotations

import hashlib
import zipfile

import pytest

from noir.domain.config import NoirConfig, reset_config
from noir.domain.enums import (
    PatchOperationType,
    ProjectStatus,
    Provenance,
)
from noir.domain.models import (
    ChangePlan,
    PatchOperation,
    PatchSet,
    ProjectInfo,
    ValidationFinding,
    ValidationResult,
)

# ── Config Tests ─────────────────────────────────────────────────────


class TestConfig:
    def setup_method(self):
        reset_config()

    def test_config_precedence_env_over_default(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOIR_DATA_DIR", str(tmp_path / "test_noir"))
        config = NoirConfig()
        assert config.data_dir == str(tmp_path / "test_noir")

    def test_resolve_tool_path_explicit(self):
        config = NoirConfig(zipalign_path="/custom/zipalign")
        assert config.resolve_tool_path("zipalign") == "/custom/zipalign"


# ── Model Tests ──────────────────────────────────────────────────────


class TestModels:
    def test_project_info_defaults(self):
        p = ProjectInfo()
        assert len(p.id) == 16
        assert p.status == ProjectStatus.CREATED
        assert p.workspace_revision == 0

    def test_change_plan_hash_deterministic(self):
        plan = ChangePlan(
            project_id="test123",
            workspace_revision=0,
            user_request="Test request",
        )
        h1 = plan.compute_hash()
        h2 = plan.compute_hash()
        assert h1 == h2
        assert len(h1) == 64

    def test_change_plan_hash_changes_with_content(self):
        plan1 = ChangePlan(
            project_id="test123",
            workspace_revision=0,
            user_request="Request A",
        )
        # Hold IDs and timestamps fixed so this tests content binding, not random IDs.
        plan2 = plan1.model_copy(update={"user_request": "Request B"})
        assert plan1.compute_hash() != plan2.compute_hash()

    def test_patch_hash_binds_operation_content(self):
        patch = PatchSet(
            plan_id="plan1",
            project_id="proj1",
            workspace_revision=0,
            operations=[
                PatchOperation(
                    relative_path="test.txt",
                    operation=PatchOperationType.CREATE_FILE,
                    new_content="hello",
                )
            ],
        )
        h = patch.compute_hash()
        assert h == patch.compute_hash()
        changed = patch.model_copy(deep=True)
        changed.operations[0].new_content = "different content"
        assert changed.compute_hash() != h

    def test_validation_has_errors(self):
        result = ValidationResult(
            project_id="test",
            workspace_revision=0,
            findings=[
                ValidationFinding(check_name="test", severity="error", message="fail"),
            ],
        )
        assert result.has_errors()

    def test_validation_no_errors(self):
        result = ValidationResult(
            project_id="test",
            workspace_revision=0,
            findings=[
                ValidationFinding(check_name="test", severity="info", message="ok"),
            ],
        )
        assert not result.has_errors()


# ── APK Validation Tests ────────────────────────────────────────────


class TestApkValidation:
    def test_file_not_found(self):
        from noir.validation.apk_validator import ApkValidationError, validate_apk

        with pytest.raises(ApkValidationError, match="not found"):
            validate_apk("/nonexistent/app.apk")

    def test_wrong_extension(self, tmp_path):
        from noir.validation.apk_validator import ApkValidationError, validate_apk

        f = tmp_path / "app.txt"
        f.write_text("not an apk")
        with pytest.raises(ApkValidationError, match="extension"):
            validate_apk(f)

    def test_empty_file(self, tmp_path):
        from noir.validation.apk_validator import ApkValidationError, validate_apk

        f = tmp_path / "empty.apk"
        f.write_bytes(b"")
        with pytest.raises(ApkValidationError, match="empty"):
            validate_apk(f)

    def test_not_zip(self, tmp_path):
        from noir.validation.apk_validator import ApkValidationError, validate_apk

        f = tmp_path / "notzip.apk"
        f.write_bytes(b"this is not a zip")
        with pytest.raises(ApkValidationError, match="not a valid ZIP"):
            validate_apk(f)

    def test_no_manifest(self, tmp_path):
        from noir.validation.apk_validator import ApkValidationError, validate_apk

        f = tmp_path / "nomanifest.apk"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("test.txt", "hello")
        with pytest.raises(ApkValidationError, match="AndroidManifest"):
            validate_apk(f)

    def test_path_traversal(self, tmp_path):
        from noir.validation.apk_validator import ApkValidationError, validate_apk

        f = tmp_path / "traversal.apk"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("AndroidManifest.xml", "<manifest/>")
            zf.writestr("../../../etc/passwd", "root")
        with pytest.raises(ApkValidationError, match="traversal"):
            validate_apk(f)

    def test_valid_apk(self, tmp_path):
        from noir.validation.apk_validator import validate_apk

        f = tmp_path / "valid.apk"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("AndroidManifest.xml", "<manifest/>")
            zf.writestr("classes.dex", b"\x00" * 10)
        result = validate_apk(f)
        assert result["valid"] is True
        assert result["has_manifest"] is True
        assert result["classification"] == "standard_apk"

    def test_resource_only_apk(self, tmp_path):
        from noir.validation.apk_validator import validate_apk

        f = tmp_path / "resource.apk"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("AndroidManifest.xml", "<manifest/>")
        result = validate_apk(f)
        assert result["valid"] is True
        assert result["classification"] == "resource_only_apk"


# ── Workspace Tests ──────────────────────────────────────────────────


class TestWorkspace:
    def test_safe_resolve_normal(self, tmp_path):
        from noir.infrastructure.filesystem.workspace import safe_resolve

        result = safe_resolve(tmp_path, "subdir/file.txt")
        assert str(result).startswith(str(tmp_path))

    def test_safe_resolve_traversal(self, tmp_path):
        from noir.infrastructure.filesystem.workspace import PathSecurityError, safe_resolve

        with pytest.raises(PathSecurityError):
            safe_resolve(tmp_path, "../../../etc/passwd")

    def test_safe_resolve_absolute(self, tmp_path):
        from noir.infrastructure.filesystem.workspace import PathSecurityError, safe_resolve

        with pytest.raises(PathSecurityError):
            safe_resolve(tmp_path, "/etc/passwd")

    def test_compute_file_hash(self, tmp_path):
        from noir.infrastructure.filesystem.workspace import compute_file_hash

        f = tmp_path / "test.txt"
        f.write_text("hello world")
        h = compute_file_hash(f)
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert h == expected

    def test_is_binary_file(self, tmp_path):
        from noir.infrastructure.filesystem.workspace import is_binary_file

        text_file = tmp_path / "text.txt"
        text_file.write_text("hello")
        assert not is_binary_file(text_file)

        bin_file = tmp_path / "binary.bin"
        bin_file.write_bytes(b"\x00\x01\x02")
        assert is_binary_file(bin_file)


# ── Manifest Parser Tests ───────────────────────────────────────────


class TestManifestParser:
    def test_parse_basic_manifest(self, tmp_path):
        from noir.analysis.manifest_parser import parse_manifest

        manifest = tmp_path / "AndroidManifest.xml"
        manifest.write_text("""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.test"
    android:versionCode="1"
    android:versionName="1.0">
    <uses-sdk android:minSdkVersion="21" android:targetSdkVersion="34" />
    <uses-permission android:name="android.permission.INTERNET" />
    <application android:name=".MyApp">
        <activity android:name=".MainActivity" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
        <service android:name=".MyService" />
    </application>
</manifest>""")
        result = parse_manifest(manifest)
        assert result["package_name"] == "com.example.test"
        assert result["version_code"] == "1"
        assert result["min_sdk"] == 21
        assert result["target_sdk"] == 34
        assert "android.permission.INTERNET" in result["permissions"]
        assert result["application_class"] == ".MyApp"
        assert len(result["components"]) == 2

        # Check launcher detection
        main_activity = [c for c in result["components"] if c.name == ".MainActivity"][0]
        assert main_activity.is_launcher is True
        assert main_activity.exported is True

    def test_missing_attributes_handled(self, tmp_path):
        from noir.analysis.manifest_parser import parse_manifest

        manifest = tmp_path / "AndroidManifest.xml"
        manifest.write_text("""<?xml version="1.0" encoding="utf-8"?>
<manifest package="com.test">
    <application>
        <activity />
    </application>
</manifest>""")
        result = parse_manifest(manifest)
        assert result["package_name"] == "com.test"
        assert result["min_sdk"] is None


# ── Smali Indexer Tests ──────────────────────────────────────────────


class TestSmaliIndexer:
    def test_scan_smali_directories(self, tmp_path):
        from noir.analysis.smali_indexer import scan_smali_directories

        (tmp_path / "smali").mkdir()
        (tmp_path / "smali_classes2").mkdir()
        (tmp_path / "res").mkdir()
        dirs = scan_smali_directories(tmp_path)
        assert "smali" in dirs
        assert "smali_classes2" in dirs
        assert "res" not in dirs

    def test_index_smali_class(self, tmp_path):
        from noir.analysis.smali_indexer import index_smali_classes

        smali_dir = tmp_path / "smali" / "com" / "example"
        smali_dir.mkdir(parents=True)
        (smali_dir / "Test.smali").write_text(
            ".class public Lcom/example/Test;\n"
            ".super Ljava/lang/Object;\n\n"
            ".method public getHello()Ljava/lang/String;\n"
            "    .locals 1\n"
            '    const-string v0, "hello"\n'
            "    return-object v0\n"
            ".end method\n"
        )
        classes = index_smali_classes(tmp_path)
        assert len(classes) == 1
        assert classes[0].descriptor == "Lcom/example/Test;"
        assert "getHello()Ljava/lang/String;" in classes[0].methods

    def test_obfuscation_detection(self):
        from noir.analysis.smali_indexer import detect_obfuscation
        from noir.domain.models import SmaliClassInfo

        # Create many short-named classes
        classes = [
            SmaliClassInfo(
                descriptor=f"La/b/{chr(97 + i)};", file_path=f"smali/a/b/{chr(97 + i)}.smali"
            )
            for i in range(20)
        ]
        indicators = detect_obfuscation(classes)
        assert len(indicators) > 0
        assert "heuristic" in indicators[0].lower()


# ── Patch Engine Tests ───────────────────────────────────────────────


class TestPatchEngine:
    def _make_workspace(self, tmp_path):
        from noir.domain.config import NoirConfig
        from noir.infrastructure.filesystem.workspace import ProjectWorkspace

        config = NoirConfig(data_dir=str(tmp_path / "data"))
        ws = ProjectWorkspace("test_proj", config)
        ws.create()
        return ws

    def test_create_file(self, tmp_path):
        ws = self._make_workspace(tmp_path)
        from noir.domain.enums import PatchOperationType
        from noir.domain.models import PatchOperation, PatchSet
        from noir.patches.engine import PatchEngine

        engine = PatchEngine(ws)
        patch = PatchSet(
            plan_id="plan1",
            project_id="test_proj",
            workspace_revision=0,
            provenance=Provenance.MANUAL,
            operations=[
                PatchOperation(
                    relative_path="test.txt",
                    operation=PatchOperationType.CREATE_FILE,
                    new_content="hello world",
                ),
            ],
        )

        errors = engine.validate_patch(patch)
        assert len(errors) == 0

        result = engine.apply_patch(patch)
        assert result["operations_applied"] == 1
        assert (ws.decoded_dir / "test.txt").read_text() == "hello world"

    def test_replace_block_exact_match(self, tmp_path):
        ws = self._make_workspace(tmp_path)
        (ws.decoded_dir / "test.txt").write_text("hello world foo bar")

        from noir.domain.enums import PatchOperationType
        from noir.domain.models import PatchOperation, PatchSet
        from noir.patches.engine import PatchEngine

        engine = PatchEngine(ws)
        patch = PatchSet(
            plan_id="plan1",
            project_id="test_proj",
            workspace_revision=0,
            provenance=Provenance.MANUAL,
            operations=[
                PatchOperation(
                    relative_path="test.txt",
                    operation=PatchOperationType.REPLACE_BLOCK,
                    match_content="hello world",
                    new_content="goodbye world",
                ),
            ],
        )

        engine.apply_patch(patch)
        assert (ws.decoded_dir / "test.txt").read_text() == "goodbye world foo bar"

    def test_ambiguous_match_rejected(self, tmp_path):
        ws = self._make_workspace(tmp_path)
        (ws.decoded_dir / "test.txt").write_text("foo bar foo baz")

        from noir.domain.enums import PatchOperationType
        from noir.domain.models import PatchOperation, PatchSet
        from noir.patches.engine import PatchEngine

        engine = PatchEngine(ws)
        patch = PatchSet(
            plan_id="plan1",
            project_id="test_proj",
            workspace_revision=0,
            provenance=Provenance.MANUAL,
            operations=[
                PatchOperation(
                    relative_path="test.txt",
                    operation=PatchOperationType.REPLACE_BLOCK,
                    match_content="foo",
                    new_content="qux",
                ),
            ],
        )

        errors = engine.validate_patch(patch)
        assert any("ambiguous" in e.lower() for e in errors)

    def test_preimage_hash_mismatch(self, tmp_path):
        ws = self._make_workspace(tmp_path)
        (ws.decoded_dir / "test.txt").write_text("original content")

        from noir.domain.enums import PatchOperationType
        from noir.domain.models import PatchOperation, PatchSet
        from noir.patches.engine import PatchEngine

        engine = PatchEngine(ws)
        patch = PatchSet(
            plan_id="plan1",
            project_id="test_proj",
            workspace_revision=0,
            provenance=Provenance.MANUAL,
            operations=[
                PatchOperation(
                    relative_path="test.txt",
                    operation=PatchOperationType.REPLACE_FILE,
                    expected_preimage_hash="wrong_hash",
                    new_content="new content",
                ),
            ],
        )

        errors = engine.validate_patch(patch)
        assert any("preimage" in e.lower() or "hash" in e.lower() for e in errors)

    def test_path_traversal_rejected(self, tmp_path):
        ws = self._make_workspace(tmp_path)

        from noir.domain.enums import PatchOperationType
        from noir.domain.models import PatchOperation, PatchSet
        from noir.patches.engine import PatchEngine

        engine = PatchEngine(ws)
        patch = PatchSet(
            plan_id="plan1",
            project_id="test_proj",
            workspace_revision=0,
            provenance=Provenance.MANUAL,
            operations=[
                PatchOperation(
                    relative_path="../../../etc/passwd",
                    operation=PatchOperationType.CREATE_FILE,
                    new_content="malicious",
                ),
            ],
        )

        errors = engine.validate_patch(patch)
        assert len(errors) > 0


# ── Process Runner Tests ─────────────────────────────────────────────


class TestProcessRunner:
    def test_run_tool_success(self):
        from noir.infrastructure.processes.runner import run_tool

        result = run_tool(["echo", "hello"], timeout=5)
        assert result.exit_code == 0
        assert "hello" in result.stdout

    def test_run_tool_not_found(self):
        from noir.infrastructure.processes.runner import run_tool

        result = run_tool(["nonexistent_command_xyz"], timeout=5)
        assert result.exit_code == -1
        assert "not found" in result.stderr.lower()

    def test_run_tool_timeout(self):
        from noir.infrastructure.processes.runner import run_tool

        result = run_tool(["sleep", "10"], timeout=1)
        assert result.timed_out is True


# ── Audit Redaction Tests ────────────────────────────────────────────


class TestAuditRedaction:
    def test_redacts_passwords(self):
        from noir.auditing.reporter import AuditReporter

        reporter = AuditReporter.__new__(AuditReporter)
        text = "password=mysecret123 and token=abc"
        redacted = reporter._redact(text)
        assert "mysecret123" not in redacted

    def test_redacts_api_keys(self):
        from noir.auditing.reporter import AuditReporter

        reporter = AuditReporter.__new__(AuditReporter)
        text = "key: AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        redacted = reporter._redact(text)
        assert "AIzaSy" not in redacted
