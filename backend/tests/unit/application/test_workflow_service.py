"""Unit tests for automated workflow service execution (R5)."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from noir.domain.config import NoirConfig
from noir.domain.enums import WorkflowStage, PatchOperationType, Provenance
from noir.domain.models import ChangePlan, JobInfo, PatchOperation, PatchSet, PlanFileChange, ProjectInfo, BuildResult
from noir.infrastructure.database.repositories import BuildRepository, JobRepository, ProjectRepository
from noir.infrastructure.filesystem.workspace import ProjectWorkspace, compute_file_hash
from noir.application.workflow_service import prepare, run_automated_workflow
from noir.infrastructure.database.engine import init_db


@pytest.fixture
def workflow_setup(tmp_path):
    cfg = NoirConfig(data_dir=str(tmp_path))
    init_db(cfg.effective_database_url)
    project_id = "test_auto_project"
    ws = ProjectWorkspace(project_id, cfg)
    ws.decoded_dir.mkdir(parents=True, exist_ok=True)
    ws.changes_dir.mkdir(parents=True, exist_ok=True)
    (ws.changes_dir / "journal").mkdir(parents=True, exist_ok=True)

    # Minimal AndroidManifest.xml
    manifest_content = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.auto">
    <application android:label="OldLabel">
        <activity android:name=".MainActivity">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""
    (ws.decoded_dir / "AndroidManifest.xml").write_text(manifest_content)

    proj = ProjectInfo(
        id=project_id,
        package_name="com.example.auto",
        workspace_revision=0,
    )
    ProjectRepository().create(proj)
    from noir.infrastructure.database.repositories import FileManifestRepository
    FileManifestRepository().save(proj.id, 0, ws.build_file_manifest())
    return cfg, project_id, ws


def test_prepare_with_auto_build_executes_rebuild_and_signing(workflow_setup, monkeypatch):
    """Triggering modification flow with auto_build automatically rebuilds and signs APK."""
    cfg, project_id, ws = workflow_setup

    # Mock AI generation to produce plan and patch
    plan = ChangePlan(
        plan_id="plan_auto_1",
        project_id=project_id,
        workspace_revision=0,
        user_request="Rename app to NewLabel",
        file_changes=[
            PlanFileChange(
                relative_path="AndroidManifest.xml",
                operation=PatchOperationType.MANIFEST_UPDATE,
            )
        ],
    )
    manifest_hash = compute_file_hash(ws.decoded_dir / "AndroidManifest.xml")
    patch = PatchSet(
        patch_id="patch_auto_1",
        plan_id="plan_auto_1",
        project_id=project_id,
        workspace_revision=0,
        provenance=Provenance.AI_GENERATED,
        operations=[
            PatchOperation(
                relative_path="AndroidManifest.xml",
                operation=PatchOperationType.MANIFEST_UPDATE,
                xml_element="application",
                xml_attributes={"android:label": "NewLabel"},
                expected_preimage_hash=manifest_hash,
            )
        ],
    )

    monkeypatch.setattr("noir.application.workflow_service.generate_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr("noir.application.workflow_service.generate_patch", lambda *args, **kwargs: patch)

    # Fake signed APK artifact
    fake_apk = ws.root / "signed.apk"
    fake_apk.write_bytes(b"PK\x03\x04fake_signed_apk_contents")
    apk_hash = compute_file_hash(fake_apk)

    build_result = BuildResult(
        build_id="build_auto_1",
        project_id=project_id,
        workspace_revision=1,
        success=True,
        signed_apk_path=str(fake_apk),
        signed_apk_hash=apk_hash,
    )
    BuildRepository().create(build_result)

    monkeypatch.setattr("noir.application.build_service.BuildService.build", lambda self, pid, job=None: build_result)
    monkeypatch.setattr("noir.infrastructure.android_tools.tools.verify_signature", lambda config, path: {"verified": True})

    job = JobRepository().create(
        JobInfo(
            project_id=project_id,
            stage=WorkflowStage.PLANNING,
            result_data={
                "operation": "workflow_prepare",
                "payload": {
                    "user_request": "Rename app to NewLabel",
                    "allow_ai_upload": True,
                    "revision": 0,
                    "auto_build": True,
                    "user_id": "test_user",
                },
            },
        )
    )

    result = prepare(cfg, job)

    # Verified signed APK container returned automatically
    assert result["build_id"] == "build_auto_1"
    assert result["signed_apk_hash"] == apk_hash
    assert Path(result["signed_apk_path"]).is_file()

    # Verify stage progression reached reporting / succeeded
    saved_job = JobRepository().get(job.job_id)
    assert saved_job.stage == WorkflowStage.REPORTING


def test_run_automated_workflow_end_to_end(workflow_setup, monkeypatch):
    """Direct invocation of run_automated_workflow executes complete end-to-end pipeline."""
    cfg, project_id, ws = workflow_setup

    plan = ChangePlan(
        plan_id="plan_auto_2",
        project_id=project_id,
        workspace_revision=0,
        user_request="Add permission",
        file_changes=[
            PlanFileChange(
                relative_path="AndroidManifest.xml",
                operation=PatchOperationType.MANIFEST_ADD,
            )
        ],
    )
    patch = PatchSet(
        patch_id="patch_auto_2",
        plan_id="plan_auto_2",
        project_id=project_id,
        workspace_revision=0,
        provenance=Provenance.AI_GENERATED,
        operations=[
            PatchOperation(
                relative_path="AndroidManifest.xml",
                operation=PatchOperationType.MANIFEST_ADD,
                xml_element="uses-permission",
                xml_attributes={"android:name": "android.permission.INTERNET"},
                expected_preimage_hash=compute_file_hash(ws.decoded_dir / "AndroidManifest.xml"),
            )
        ],
    )

    monkeypatch.setattr("noir.application.workflow_service.generate_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr("noir.application.workflow_service.generate_patch", lambda *args, **kwargs: patch)

    fake_apk = ws.root / "output_signed.apk"
    fake_apk.write_bytes(b"PK\x03\x04output_signed_contents")
    apk_hash = compute_file_hash(fake_apk)

    build_result = BuildResult(
        build_id="build_auto_2",
        project_id=project_id,
        workspace_revision=1,
        success=True,
        signed_apk_path=str(fake_apk),
        signed_apk_hash=apk_hash,
    )
    BuildRepository().create(build_result)

    monkeypatch.setattr("noir.application.build_service.BuildService.build", lambda self, pid, job=None: build_result)
    monkeypatch.setattr("noir.infrastructure.android_tools.tools.verify_signature", lambda config, path: {"verified": True})

    result = run_automated_workflow(
        cfg,
        project_id,
        "Add permission",
        user_id="alice",
    )

    assert result["build_id"] == "build_auto_2"
    assert result["signed_apk_hash"] == apk_hash
