"""Adversarial tests for automated rebuild, signing pipeline, and job status reporting (Round 2)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from noir.application.jobs import TaskQueue
from noir.application.workflow_service import _execute_finish_steps
from noir.domain.config import NoirConfig
from noir.domain.enums import EventSeverity, JobState, PatchOperationType, Provenance, WorkflowStage
from noir.domain.models import (
    BuildResult,
    ChangePlan,
    JobInfo,
    PatchOperation,
    PatchSet,
    PlanFileChange,
    ProjectInfo,
)
from noir.infrastructure.database.engine import init_db
from noir.infrastructure.database.repositories import (
    BuildRepository,
    EventRepository,
    JobRepository,
    ProjectRepository,
)
from noir.infrastructure.filesystem.workspace import ProjectWorkspace, compute_file_hash


@pytest.fixture
def pipeline_setup(tmp_path):
    cfg = NoirConfig(data_dir=str(tmp_path))
    init_db(cfg.effective_database_url)
    project_id = "test_adv_pipe_proj"
    ws = ProjectWorkspace(project_id, cfg)
    ws.decoded_dir.mkdir(parents=True, exist_ok=True)
    ws.changes_dir.mkdir(parents=True, exist_ok=True)
    (ws.changes_dir / "journal").mkdir(parents=True, exist_ok=True)

    manifest_content = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.pipe">
    <application android:name=".App" />
</manifest>"""
    (ws.decoded_dir / "AndroidManifest.xml").write_text(manifest_content)

    proj = ProjectInfo(id=project_id, package_name="com.example.pipe", workspace_revision=0)
    ProjectRepository().create(proj)
    from noir.infrastructure.database.repositories import FileManifestRepository

    FileManifestRepository().save(proj.id, 0, ws.build_file_manifest())
    return cfg, project_id, ws


def test_workflow_pipeline_missing_signed_apk_raises_clear_error(pipeline_setup, monkeypatch):
    """If SigningService fails to produce an artifact, _execute_finish_steps raises a clear ValueError, not TypeError."""
    cfg, project_id, ws = pipeline_setup

    plan = ChangePlan(
        plan_id="plan_pipe_1",
        project_id=project_id,
        workspace_revision=0,
        user_request="Test",
        file_changes=[
            PlanFileChange(
                relative_path="AndroidManifest.xml", operation=PatchOperationType.MANIFEST_UPDATE
            )
        ],
    )
    manifest_hash = compute_file_hash(ws.decoded_dir / "AndroidManifest.xml")
    patch = PatchSet(
        patch_id="patch_pipe_1",
        plan_id="plan_pipe_1",
        project_id=project_id,
        workspace_revision=0,
        provenance=Provenance.AI_GENERATED,
        operations=[
            PatchOperation(
                relative_path="AndroidManifest.xml",
                operation=PatchOperationType.MANIFEST_UPDATE,
                xml_element="application",
                xml_match_attributes={"android:name": ".App"},
                xml_attributes={"android:name": ".NewApp"},
                expected_preimage_hash=manifest_hash,
            )
        ],
    )

    build_result = BuildResult(
        build_id="build_pipe_1",
        project_id=project_id,
        workspace_revision=1,
        success=True,
        signed_apk_path=None,  # Missing signed APK path!
        signed_apk_hash=None,
    )
    BuildRepository().create(build_result)

    monkeypatch.setattr(
        "noir.application.build_service.BuildService.build",
        lambda self, pid, job=None: build_result,
    )
    # Mock SigningService.sign returning build with no signed_apk_path
    monkeypatch.setattr(
        "noir.application.signing_service.SigningService.sign",
        lambda self, pid, bid, prof, confirmed=True: build_result,
    )
    monkeypatch.setattr(
        "noir.application.access_service.AccessService.user",
        lambda self, user_id: {"user_id": user_id},
    )
    monkeypatch.setattr(
        "noir.application.access_service.AccessService.ensure_personal_signer",
        lambda self, config, user_id: SimpleNamespace(name="debug"),
    )

    job = JobInfo(
        project_id=project_id,
        stage=WorkflowStage.PLANNING,
        result_data={"operation": "workflow_prepare", "payload": {}},
    )
    JobRepository().create(job)

    with pytest.raises(ValueError, match="Signed APK artifact not found"):
        _execute_finish_steps(
            cfg, job, project_id=project_id, plan=plan, patch=patch, user_id="test_user"
        )


def test_execute_job_failure_emits_error_audit_event(pipeline_setup, monkeypatch):
    """When a background job fails during execution, an ERROR AuditEvent is emitted to the audit trail."""
    cfg, project_id, ws = pipeline_setup

    job = JobInfo(
        project_id=project_id,
        stage=WorkflowStage.PLANNING,
        result_data={
            "operation": "workflow_prepare",
            "payload": {"user_request": "fail this job", "revision": 0, "allow_ai_upload": True},
        },
    )
    JobRepository().create(job)

    # Force prepare to raise an error
    monkeypatch.setattr(
        "noir.application.workflow_service.prepare",
        MagicMock(side_effect=RuntimeError("Simulated build crash")),
    )

    queue = TaskQueue(cfg)
    queue.execute(job)

    # Verify job state
    saved_job = JobRepository().get(job.job_id)
    assert saved_job.state == JobState.FAILED
    assert "Simulated build crash" in saved_job.error_message

    # Verify ERROR audit event was recorded
    events = EventRepository().list_by_project(project_id)
    error_events = [e for e in events if e.severity == EventSeverity.ERROR]
    assert len(error_events) >= 1
    assert "Simulated build crash" in error_events[0].message
    assert error_events[0].job_id == job.job_id


def test_execute_job_cancelled_state_preserved(pipeline_setup, monkeypatch):
    """When a job is cancelled, its terminal state is JobState.CANCELLED rather than FAILED."""
    cfg, project_id, ws = pipeline_setup

    job = JobInfo(
        project_id=project_id,
        stage=WorkflowStage.PLANNING,
        result_data={
            "operation": "workflow_prepare",
            "payload": {"user_request": "cancel me", "revision": 0},
            "cancel_requested": True,
        },
    )
    JobRepository().create(job)

    # Force prepare to raise cancellation
    monkeypatch.setattr(
        "noir.application.workflow_service.prepare",
        MagicMock(side_effect=RuntimeError("Job cancelled before execution")),
    )

    queue = TaskQueue(cfg)
    queue.execute(job)

    saved_job = JobRepository().get(job.job_id)
    assert saved_job.state == JobState.CANCELLED


def test_taskqueue_sets_correct_initial_stage_for_workflow_automated(pipeline_setup):
    """Submitting workflow_automated initializes stage as PLANNING, not REBUILDING."""
    cfg, project_id, ws = pipeline_setup

    queue = TaskQueue(cfg)
    job = queue._submit(
        "workflow_automated",
        project_id,
        {"user_request": "Change logo", "revision": 0},
    )

    assert job.stage == WorkflowStage.PLANNING


def test_taskqueue_sets_correct_initial_stage_for_workflow_finish(pipeline_setup):
    """Submitting workflow_finish initializes stage as APPLYING_PATCH, not REBUILDING."""
    cfg, project_id, ws = pipeline_setup

    queue = TaskQueue(cfg)
    job = queue._submit(
        "workflow_finish",
        project_id,
        {"plan_id": "p1", "patch_id": "p2"},
    )

    assert job.stage == WorkflowStage.APPLYING_PATCH


def test_automated_workflow_api_endpoint(pipeline_setup):
    """POST /v1/projects/{project_id}/workflow/automated submits workflow_automated job."""
    import hashlib
    import secrets

    from fastapi.testclient import TestClient

    from noir.api.app import create_app
    from noir.domain.models import ApiToken
    from noir.infrastructure.database.repositories import TokenRepository

    cfg, project_id, ws = pipeline_setup
    token = secrets.token_urlsafe(24)
    TokenRepository().create(ApiToken(token_hash=hashlib.sha256(token.encode()).hexdigest()))

    app = create_app(cfg)
    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})

    response = client.post(
        f"/v1/projects/{project_id}/workflow/automated",
        json={
            "user_request": "Automated pipeline test",
            "allow_ai_upload": True,
            "revision": 0,
        },
        headers={"Idempotency-Key": "test_auto_key_1"},
    )

    assert response.status_code == 202
    data = response.json()
    assert data["project_id"] == project_id
    assert data["result_data"]["operation"] == "workflow_automated"
    assert data["stage"] == "planning"
    client.close()
