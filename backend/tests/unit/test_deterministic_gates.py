"""Comprehensive unit tests for Milestone 1: Deterministic Gates G1–G4 and Rules Engine.

Covers:
- Gate 1: Deliverable Form & Zero-AI Routing
- Gate 2: Host Toolchain & Environment Truth (AAPT2, APKTool, JDK 17+, xattrs)
- Gate 3: Code Location & Execution Layer Boundary (Cross-layer regression prevention)
- Gate 4: Unmodified Roundtrip Build Control
- NoirConfig SDK auto-detection cascade
- DoctorService capabilities and truth reporting
- Integration with ai_service.py and deterministic_service.py
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from noir.application.deterministic_service import verify_deterministic_preflight
from noir.application.doctor import (
    DoctorService,
    _check_java,
    _check_xattr_support,
    _parse_java_major_version,
)
from noir.application.gates import (
    DeliverableForm,
    ExecutionLayer,
    GateResult,
    GateStatus,
    PreflightGateEngine,
    PreflightReport,
)
from noir.config import NoirConfig, reset_config
from noir.domain.models import AnalysisResult, DoctorReport, ToolCheck
from noir.infrastructure.filesystem.workspace import ProjectWorkspace

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def clean_config(tmp_path):
    reset_config()
    cfg = NoirConfig(
        _env_file=None,
        data_dir=str(tmp_path),
        gemini_api_key="test-key",
    )
    yield cfg
    reset_config()


@pytest.fixture
def mock_workspace(tmp_path):
    config = NoirConfig(_env_file=None, data_dir=str(tmp_path), gemini_api_key="test-key")
    ws = ProjectWorkspace("test_gates_project", config)
    ws.create()

    manifest_content = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.test">
    <application android:label="OriginalApp">
        <activity android:name=".MainActivity" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>"""
    (ws.decoded_dir / "AndroidManifest.xml").write_text(manifest_content, encoding="utf-8")
    (ws.decoded_dir / "apktool.yml").write_text(
        "version: 3.0.3\napkFileName: test.apk\n", encoding="utf-8"
    )
    return ws


# ── Config Auto-Detection Tests ──────────────────────────────────────


def test_config_auto_detects_homebrew_commandlinetools():
    """NoirConfig detects /opt/homebrew/share/android-commandlinetools when available."""
    brew_path = Path("/opt/homebrew/share/android-commandlinetools")
    cfg = NoirConfig(_env_file=None)
    if brew_path.is_dir():
        assert cfg.android_sdk_dir == str(brew_path)
        aapt2_resolved = cfg.resolve_tool_path("aapt2")
        assert Path(aapt2_resolved).is_file()
        assert "aapt2" in aapt2_resolved


def test_config_explicit_env_override(monkeypatch, tmp_path):
    """NOIR_ANDROID_SDK_DIR environment variable takes precedence over auto-detection."""
    fake_sdk = tmp_path / "custom_sdk"
    fake_sdk.mkdir()
    monkeypatch.setenv("NOIR_ANDROID_SDK_DIR", str(fake_sdk))

    cfg = NoirConfig(_env_file=None)
    assert cfg.android_sdk_dir == str(fake_sdk)


def test_config_resolve_tool_path_scans_build_tools_versions(tmp_path):
    """resolve_tool_path scans version directories in build-tools when specified version is absent."""
    sdk_dir = tmp_path / "android_sdk"
    tools_dir = sdk_dir / "build-tools" / "35.0.0"
    tools_dir.mkdir(parents=True)
    fake_aapt2 = tools_dir / "aapt2"
    fake_aapt2.write_text("#!/bin/sh\nexit 0\n")
    fake_aapt2.chmod(0o755)

    cfg = NoirConfig(_env_file=None, android_sdk_dir=str(sdk_dir), build_tools_version="99.0.0")
    resolved = cfg.resolve_tool_path("aapt2")
    assert resolved == str(fake_aapt2)


def test_noir_config_facade_import():
    """Verify that importing NoirConfig and singletons from noir.config works cleanly."""
    from noir.config import NoirConfig as FacadeConfig
    from noir.config import get_config as facade_get

    cfg = facade_get()
    assert isinstance(cfg, FacadeConfig)


# ── Doctor Capabilities & JDK 17+ Tests ──────────────────────────────


def test_java_version_parser():
    """_parse_java_major_version accurately extracts Java major versions."""
    assert _parse_java_major_version('openjdk version "21.0.11" 2026-04-21') == 21
    assert _parse_java_major_version('java version "17.0.2" 2022-01-18 LTS') == 17
    assert _parse_java_major_version('openjdk version "11.0.12"') == 11
    assert _parse_java_major_version('java version "1.8.0_292"') == 8
    assert _parse_java_major_version("openjdk 21.0.11") == 21
    assert _parse_java_major_version("invalid string") is None


def test_doctor_java_rejects_jdk_below_17():
    """_check_java flags JDKs below version 17 as unavailable with clear error message."""
    mock_result = MagicMock()
    mock_result.stdout = 'openjdk version "11.0.12" 2021-07-20\nOpenJDK Runtime Environment'
    mock_result.stderr = ""

    with (
        patch("noir.application.doctor.run_tool", return_value=mock_result),
        patch("noir.application.doctor.shutil.which", return_value="/usr/bin/java"),
    ):
        check = _check_java(NoirConfig(_env_file=None))
        assert check.available is False
        assert "JDK 17+ required" in check.message
        assert "found Java 11" in check.message


def test_doctor_java_accepts_jdk_17_plus():
    """_check_java accepts JDK 17 and JDK 21."""
    mock_result = MagicMock()
    mock_result.stdout = 'openjdk version "21.0.11" 2026-04-21\nOpenJDK 64-Bit Server VM'
    mock_result.stderr = ""

    with (
        patch("noir.application.doctor.run_tool", return_value=mock_result),
        patch("noir.application.doctor.shutil.which", return_value="/usr/bin/java"),
    ):
        check = _check_java(NoirConfig(_env_file=None))
        assert check.available is True
        assert check.message == "OK"


def test_doctor_xattr_support_check():
    """_check_xattr_support checks for os.listxattr or xattr CLI."""
    check = _check_xattr_support()
    assert check.name == "xattr"
    assert check.available is True
    assert "xattr" in check.message.lower()


def test_doctor_service_run_doctor(clean_config):
    """DoctorService returns a complete DoctorReport with true toolchain status."""
    service = DoctorService(clean_config)
    report = service.run_doctor()

    assert isinstance(report, DoctorReport)
    names = {c.name.lower() for c in report.checks}
    assert "java" in names
    assert "apktool" in names
    assert "aapt2" in names
    assert "xattr" in names


def test_doctor_service_check_toolchain_for_build(clean_config):
    """DoctorService.check_toolchain_for_build returns tuple of (bool, diagnostics)."""
    service = DoctorService(clean_config)
    capable, diagnostics = service.check_toolchain_for_build()
    if capable:
        assert diagnostics == []
    else:
        assert len(diagnostics) > 0


# ── Gate 1 Tests: Form Classification & Zero-AI Routing ───────────────


def test_gate_1_form_classification_rebuilt_apk():
    engine = PreflightGateEngine()
    result = engine.evaluate_g1("Change button text and style in app")
    assert result.passed is True
    assert result.status == GateStatus.PASSED
    assert result.deliverable_form == DeliverableForm.REBUILT_APK


def test_gate_1_form_classification_rpc():
    engine = PreflightGateEngine()
    result = engine.evaluate_g1("Emulate signing routine to produce valid auth tokens via RPC")
    assert result.passed is True
    assert result.deliverable_form == DeliverableForm.RPC
    assert result.is_zero_ai is False


def test_gate_1_form_classification_report():
    engine = PreflightGateEngine()
    result = engine.evaluate_g1(
        "Bypass server payment and unlock subscription via server-side authority"
    )
    assert result.passed is True
    assert result.deliverable_form == DeliverableForm.REPORT
    assert "server-authoritative" in result.message


def test_gate_1_form_classification_module():
    engine = PreflightGateEngine()
    result = engine.evaluate_g1("Create an LSPosed module to hook license check")
    assert result.passed is True
    assert result.deliverable_form == DeliverableForm.MODULE


def test_gate_1_zero_ai_routing_rename():
    engine = PreflightGateEngine()
    result = engine.evaluate_g1("Rename app to NoirApp")
    assert result.passed is True
    assert result.is_zero_ai is True
    assert result.deliverable_form == DeliverableForm.REBUILT_APK
    assert result.metadata["routed_handler"] == "DeterministicService"


def test_gate_1_zero_ai_routing_toast():
    engine = PreflightGateEngine()
    result = engine.evaluate_g1('Show toast saying "Welcome to Noir"')
    assert result.passed is True
    assert result.is_zero_ai is True
    assert result.deliverable_form == DeliverableForm.REBUILT_APK


def test_gate_1_zero_ai_routing_launch_url():
    engine = PreflightGateEngine()
    result = engine.evaluate_g1("Redirect launch to https://noir.local")
    assert result.passed is True
    assert result.is_zero_ai is True


def test_gate_1_zero_ai_routing_permission():
    engine = PreflightGateEngine()
    result = engine.evaluate_g1("Grant android.permission.CAMERA")
    assert result.passed is True
    assert result.is_zero_ai is True


def test_gate_1_fallback_to_ai_on_complex_request():
    engine = PreflightGateEngine()
    result = engine.evaluate_g1("Reverse engineer algorithm and modify smali state machine")
    assert result.passed is True
    assert result.is_zero_ai is False


# ── Gate 2 Tests: Host Toolchain & Environment Truth ──────────────────


def test_gate_2_success_when_toolchain_ready(clean_config):
    engine = PreflightGateEngine(clean_config)
    result = engine.evaluate_g2()
    # If the local machine has complete toolchain, G2 passes
    if clean_config.android_sdk_dir:
        assert result.passed is True
        assert result.status == GateStatus.PASSED
        assert "verified" in result.message.lower()


def test_gate_2_blocked_when_tool_missing():
    mock_doctor = MagicMock()
    mock_report = DoctorReport(
        checks=[
            ToolCheck(
                name="Java", available=True, path="/bin/java", required_for="required_for_import"
            ),
            ToolCheck(
                name="APKTool",
                available=True,
                path="/bin/apktool",
                required_for="required_for_import",
            ),
            ToolCheck(
                name="aapt2",
                available=False,
                required_for="required_for_build",
                message="aapt2 missing",
            ),
        ],
        all_required_available=False,
        build_capable=False,
        ai_configured=True,
        device_capable=False,
        summary="Missing tools",
    )
    mock_doctor.run_doctor.return_value = mock_report

    engine = PreflightGateEngine(doctor_service=mock_doctor)
    result = engine.evaluate_g2()

    assert result.passed is False
    assert result.status == GateStatus.BLOCKED
    assert any("aapt2" in d.lower() for d in result.diagnostics)


def test_gate_2_blocked_when_jdk_insufficient():
    mock_doctor = MagicMock()
    mock_report = DoctorReport(
        checks=[
            ToolCheck(
                name="Java",
                available=False,
                message="JDK 17+ required (found Java 11)",
                required_for="required_for_import",
            ),
            ToolCheck(
                name="APKTool",
                available=True,
                path="/bin/apktool",
                required_for="required_for_import",
            ),
            ToolCheck(
                name="aapt2", available=True, path="/bin/aapt2", required_for="required_for_build"
            ),
        ],
        all_required_available=False,
        build_capable=False,
        ai_configured=True,
        device_capable=False,
        summary="Missing JDK",
    )
    mock_doctor.run_doctor.return_value = mock_report

    engine = PreflightGateEngine(doctor_service=mock_doctor)
    result = engine.evaluate_g2()

    assert result.passed is False
    assert result.status == GateStatus.BLOCKED
    assert any("jdk 17+" in d.lower() for d in result.diagnostics)


# ── Gate 3 Tests: Code Location & Execution Layer Boundary ────────────


def test_gate_3_detects_dex_layer():
    engine = PreflightGateEngine()
    analysis = AnalysisResult(project_id="test", runtimes={"dalvik"})
    result = engine.evaluate_g3("Modify authentication flow", analysis=analysis)

    assert result.passed is True
    assert result.status == GateStatus.PASSED
    assert result.execution_layer == ExecutionLayer.DEX


def test_gate_3_detects_flutter_layer():
    engine = PreflightGateEngine()
    analysis = AnalysisResult(project_id="test", runtimes={"flutter"})
    result = engine.evaluate_g3("Analyze flutter logic", analysis=analysis)

    assert result.passed is True
    assert result.execution_layer == ExecutionLayer.FLUTTER_DART


def test_gate_3_manifest_rename_safe_on_flutter():
    engine = PreflightGateEngine()
    analysis = AnalysisResult(project_id="test", runtimes={"flutter"})
    result = engine.evaluate_g3("Rename app to NoirFlutter", analysis=analysis)

    assert result.passed is True
    assert result.execution_layer == ExecutionLayer.MANIFEST_RESOURCE


def test_gate_3_blocks_dex_patch_on_flutter():
    engine = PreflightGateEngine()
    analysis = AnalysisResult(project_id="test", runtimes={"flutter"})
    result = engine.evaluate_g3("Patch smali bytecode in MainActivity", analysis=analysis)

    assert result.passed is False
    assert result.status == GateStatus.BLOCKED
    assert "cross-layer regression blocked" in result.message.lower()
    assert "flutter_dart" in result.diagnostics[0].lower()


def test_gate_3_blocks_dex_patch_on_unity_il2cpp():
    engine = PreflightGateEngine()
    analysis = AnalysisResult(project_id="test", runtimes={"il2cpp"})
    result = engine.evaluate_g3("Apply dex patch to game logic class", analysis=analysis)

    assert result.passed is False
    assert result.status == GateStatus.BLOCKED
    assert "cross-layer regression blocked" in result.message.lower()
    assert "unity_il2cpp" in result.diagnostics[0].lower()


# ── Gate 4 Tests: Unmodified Roundtrip Build Control ──────────────────


def test_gate_4_passes_on_valid_workspace(mock_workspace):
    engine = PreflightGateEngine()
    result = engine.evaluate_g4(workspace=mock_workspace)

    assert result.passed is True
    assert result.status == GateStatus.PASSED
    assert "verified" in result.message.lower()


def test_gate_4_fails_on_missing_workspace(tmp_path):
    engine = PreflightGateEngine()
    non_existent = tmp_path / "does_not_exist"
    result = engine.evaluate_g4(workspace=non_existent)

    assert result.passed is False
    assert result.status == GateStatus.FAILED
    assert "does not exist" in result.message.lower()


def test_gate_4_fails_on_missing_manifest(tmp_path):
    engine = PreflightGateEngine()
    decoded = tmp_path / "decoded"
    decoded.mkdir()
    (decoded / "apktool.yml").write_text("version: 3.0.3\n")

    result = engine.evaluate_g4(workspace=decoded)
    assert result.passed is False
    assert result.status == GateStatus.FAILED
    assert "androidmanifest.xml missing" in result.message.lower()


def test_gate_4_fails_on_corrupt_manifest_xml(tmp_path):
    engine = PreflightGateEngine()
    decoded = tmp_path / "decoded"
    decoded.mkdir()
    (decoded / "AndroidManifest.xml").write_text("<manifest><unclosed_tag></manifest>")
    (decoded / "apktool.yml").write_text("version: 3.0.3\n")

    result = engine.evaluate_g4(workspace=decoded)
    assert result.passed is False
    assert result.status == GateStatus.FAILED
    assert "corrupt" in result.message.lower()


def test_gate_4_fails_on_empty_apktool_yml(tmp_path):
    engine = PreflightGateEngine()
    decoded = tmp_path / "decoded"
    decoded.mkdir()
    (decoded / "AndroidManifest.xml").write_text("<manifest></manifest>")
    (decoded / "apktool.yml").write_text("   \n")

    result = engine.evaluate_g4(workspace=decoded)
    assert result.passed is False
    assert result.status == GateStatus.FAILED
    assert "empty" in result.message.lower()


# ── End-to-End PreflightGateEngine Pipeline Tests ─────────────────────


def test_preflight_engine_all_passed(mock_workspace, clean_config):
    engine = PreflightGateEngine(clean_config)
    report = engine.evaluate_preflight(
        "Rename app to SuperApp",
        workspace=mock_workspace,
    )
    assert isinstance(report, PreflightReport)
    assert report.all_passed is True
    assert report.is_zero_ai is True
    assert report.failed_gate is None
    assert len(report.results) == 4
    assert [r.gate_id for r in report.results] == ["G1", "G2", "G3", "G4"]


def test_preflight_engine_captures_failure(mock_workspace):
    mock_doctor = MagicMock()
    mock_report = DoctorReport(
        checks=[
            ToolCheck(
                name="Java", available=True, path="/bin/java", required_for="required_for_import"
            ),
            ToolCheck(
                name="APKTool",
                available=False,
                required_for="required_for_import",
                message="apktool missing",
            ),
        ],
        all_required_available=False,
        build_capable=False,
        ai_configured=True,
        device_capable=False,
        summary="Missing apktool",
    )
    mock_doctor.run_doctor.return_value = mock_report

    engine = PreflightGateEngine(doctor_service=mock_doctor)
    report = engine.evaluate_preflight("Rename app to SuperApp", workspace=mock_workspace)

    assert report.all_passed is False
    assert report.failed_gate is not None
    assert report.failed_gate.gate_id == "G2"
    assert "apktool missing" in report.summary.lower()


def test_verify_deterministic_preflight_helper(mock_workspace, clean_config):
    report = verify_deterministic_preflight(
        "Rename app to NoirTest",
        workspace=mock_workspace,
        config=clean_config,
    )
    assert isinstance(report, PreflightReport)
    assert report.all_passed is True
    assert report.is_zero_ai is True


def test_ai_service_blocks_on_gate_2_toolchain_failure(mock_workspace, clean_config, monkeypatch):
    """ai_service.generate_plan raises PlanServiceError when G2 fails."""
    from noir.application.ai_service import PlanServiceError, generate_plan

    # Mock ProjectRepository.get to return a valid project
    mock_proj = MagicMock()
    mock_proj.workspace_revision = 0
    monkeypatch.setattr(
        "noir.application.ai_service.ProjectRepository.get", lambda self, pid: mock_proj
    )
    monkeypatch.setattr("noir.application.ai_service.require_clean_workspace", lambda *args: None)

    # Mock gate engine G2 failure
    mock_engine = MagicMock()
    mock_engine.evaluate_g1.return_value = GateResult(
        gate_id="G1", name="G1", passed=True, status=GateStatus.PASSED
    )
    mock_engine.evaluate_g2.return_value = GateResult(
        gate_id="G2",
        name="Toolchain",
        passed=False,
        status=GateStatus.BLOCKED,
        message="AAPT2 missing from host",
    )
    monkeypatch.setattr(
        "noir.application.gates.PreflightGateEngine", lambda *args, **kwargs: mock_engine
    )

    with pytest.raises(PlanServiceError, match="Preflight gate G2 failed: AAPT2 missing from host"):
        generate_plan(clean_config, "test_gates_project", "Rename app to NewName", consent=True)
