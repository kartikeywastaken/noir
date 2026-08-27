"""Integration tests for NOIR — CLI, API, database, and services."""

from __future__ import annotations

import hashlib

import pytest

from noir.domain.config import NoirConfig, reset_config
from noir.domain.enums import PatchOperationType, Provenance
from noir.domain.models import (
    ChangePlan,
    PatchOperation,
    PatchSet,
    PlanFileChange,
    ProjectInfo,
)
from noir.infrastructure.database.engine import init_db


@pytest.fixture
def test_config(tmp_path):
    """Create a test configuration with temp directories."""
    reset_config()
    config = NoirConfig(
        data_dir=str(tmp_path / "data"),
        ai_provider="none",
    )
    config.ensure_directories()
    init_db(config.effective_database_url)
    return config


class TestDatabase:
    """Test database operations."""

    def test_project_crud(self, test_config):
        from noir.infrastructure.database.repositories import ProjectRepository

        repo = ProjectRepository()

        project = ProjectInfo(package_name="com.test")
        repo.create(project)

        loaded = repo.get(project.id)
        assert loaded is not None
        assert loaded.package_name == "com.test"

        loaded.package_name = "com.test.updated"
        repo.update(loaded)

        reloaded = repo.get(project.id)
        assert reloaded.package_name == "com.test.updated"

    def test_project_list(self, test_config):
        from noir.infrastructure.database.repositories import ProjectRepository

        repo = ProjectRepository()

        repo.create(ProjectInfo(package_name="com.a"))
        repo.create(ProjectInfo(package_name="com.b"))

        projects = repo.list_all()
        assert len(projects) >= 2

    def test_event_persistence(self, test_config):
        from noir.domain.enums import EventSeverity
        from noir.domain.models import AuditEvent
        from noir.infrastructure.database.repositories import EventRepository

        repo = EventRepository()
        evt = AuditEvent(
            project_id="test",
            severity=EventSeverity.INFO,
            message="Test event",
            metadata={"key": "value"},
        )
        repo.create(evt)

        events = repo.list_by_project("test")
        assert len(events) >= 1
        assert events[0].message == "Test event"
        assert events[0].metadata.get("key") == "value"

    def test_approval_lifecycle(self, test_config):
        from noir.domain.enums import ApprovalScope
        from noir.domain.models import ApprovalRecord
        from noir.infrastructure.database.repositories import ApprovalRepository

        repo = ApprovalRepository()
        approval = ApprovalRecord(
            project_id="test",
            scope=ApprovalScope.PLAN,
            workspace_revision=0,
            target_hash="abc123",
            target_id="plan1",
        )
        repo.create(approval)

        found = repo.find_valid("test", ApprovalScope.PLAN, "abc123", 0)
        assert found is not None

        # Wrong hash
        not_found = repo.find_valid("test", ApprovalScope.PLAN, "wrong", 0)
        assert not_found is None

        # Invalidate
        count = repo.invalidate_for_project("test", ApprovalScope.PLAN)
        assert count >= 1

    def test_token_hashing(self, test_config):
        from noir.domain.models import ApiToken
        from noir.infrastructure.database.repositories import TokenRepository

        repo = TokenRepository()
        raw = "test_token_123"
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        token = ApiToken(token_hash=token_hash, name="test")
        repo.create(token)

        found = repo.find_by_hash(token_hash)
        assert found is not None

        not_found = repo.find_by_hash("wrong_hash")
        assert not_found is None


class TestPlanPatchWorkflow:
    """Test the plan → approve → patch → approve → apply workflow."""

    def test_full_workflow(self, test_config, tmp_path):
        from noir.application.patch_service import PatchService, PlanService
        from noir.infrastructure.database.repositories import (
            FileManifestRepository,
            ProjectRepository,
        )
        from noir.infrastructure.filesystem.workspace import ProjectWorkspace

        # Create project
        project = ProjectInfo(package_name="com.test")
        ProjectRepository().create(project)

        # Set up workspace
        ws = ProjectWorkspace(project.id, test_config)
        ws.create()
        (ws.decoded_dir / "test.txt").write_text("original content")
        manifest = ws.build_file_manifest()
        FileManifestRepository().save(project.id, 0, manifest)

        # Create plan
        plan = ChangePlan(
            project_id=project.id,
            workspace_revision=0,
            user_request="Change test content",
            intended_outcome="Replace original with modified",
            file_changes=[
                PlanFileChange(
                    relative_path="test.txt",
                    operation=PatchOperationType.REPLACE_BLOCK,
                    description="Replace content",
                )
            ],
        )
        plan_svc = PlanService(test_config)
        plan = plan_svc.create_plan(plan)
        plan_hash = plan.compute_hash()

        # Approve plan
        approval = plan_svc.approve_plan(project.id, plan.plan_id, plan_hash)
        assert approval.status.value == "approved"

        # Create patch
        patch = PatchSet(
            plan_id=plan.plan_id,
            project_id=project.id,
            workspace_revision=0,
            provenance=Provenance.MANUAL,
            operations=[
                PatchOperation(
                    relative_path="test.txt",
                    operation=PatchOperationType.REPLACE_BLOCK,
                    match_content="original content",
                    new_content="modified content",
                )
            ],
        )
        patch_svc = PatchService(test_config)
        patch = patch_svc.store_patch(patch)
        patch_hash = patch.compute_hash()

        # Approve patch
        patch_approval = patch_svc.approve_patch(project.id, patch.patch_id, patch_hash)
        assert patch_approval.status.value == "approved"

        # Apply patch
        result = patch_svc.apply_patch(project.id, patch.patch_id)
        assert result["operations_applied"] == 1
        assert (ws.decoded_dir / "test.txt").read_text() == "modified content"

    def test_stale_plan_rejected(self, test_config):
        from noir.application.patch_service import PlanService, PlanServiceError
        from noir.infrastructure.database.repositories import ProjectRepository

        # Create project at revision 0
        project = ProjectInfo(package_name="com.test", workspace_revision=0)
        ProjectRepository().create(project)

        plan = ChangePlan(
            project_id=project.id,
            workspace_revision=0,
            user_request="Test",
        )
        plan_svc = PlanService(test_config)
        plan = plan_svc.create_plan(plan)
        plan_hash = plan.compute_hash()

        # Advance project revision AFTER plan creation (simulates workspace change)
        project.workspace_revision = 1
        ProjectRepository().update(project)

        with pytest.raises(PlanServiceError, match="stale"):
            plan_svc.approve_plan(project.id, plan.plan_id, plan_hash)


class TestManualEditing:
    def test_manual_edit_workflow(self, test_config):
        from noir.application.manual_service import ManualService
        from noir.infrastructure.database.repositories import (
            FileManifestRepository,
            ProjectRepository,
        )
        from noir.infrastructure.filesystem.workspace import ProjectWorkspace

        project = ProjectInfo(package_name="com.test")
        ProjectRepository().create(project)

        ws = ProjectWorkspace(project.id, test_config)
        ws.create()
        (ws.decoded_dir / "file.txt").write_text("before")
        FileManifestRepository().save(project.id, 0, ws.build_file_manifest())

        svc = ManualService(test_config)

        # Begin session
        session = svc.begin_session(project.id)
        assert session.active

        # Simulate edit
        (ws.decoded_dir / "file.txt").write_text("after")

        # Record changes
        result = svc.record_changes(project.id, "Changed file.txt")
        assert result["changes_detected"] == 1
        assert "file.txt" in result["modified"]


class TestAPIAuth:
    """Test API authentication."""

    def test_api_requires_token(self, test_config):
        from fastapi.testclient import TestClient

        from noir.api.app import create_app

        app = create_app(test_config)
        client = TestClient(app)

        # Should fail without token
        resp = client.get("/v1/projects")
        assert resp.status_code == 401

    def test_api_with_valid_token(self, test_config):
        from fastapi.testclient import TestClient

        from noir.api.app import create_app
        from noir.domain.models import ApiToken
        from noir.infrastructure.database.repositories import TokenRepository

        # Create token
        raw = "test_token_abc123"
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        TokenRepository().create(ApiToken(token_hash=token_hash))

        app = create_app(test_config)
        client = TestClient(app)

        resp = client.get(
            "/v1/projects",
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 200

    def test_api_health_no_auth(self, test_config):
        from fastapi.testclient import TestClient

        from noir.api.app import create_app

        app = create_app(test_config)
        client = TestClient(app)

        resp = client.get("/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
