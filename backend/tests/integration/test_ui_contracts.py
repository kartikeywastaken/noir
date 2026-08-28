"""UI-facing API contracts, exercised with isolated workspaces and no AI calls."""

import hashlib
import secrets

import pytest
from fastapi.testclient import TestClient

from noir.api.app import create_app
from noir.application.patch_service import PatchService, PlanService
from noir.domain.config import NoirConfig, reset_config
from noir.domain.enums import PatchOperationType, Provenance
from noir.domain.models import (
    ApiToken,
    ChangePlan,
    PatchOperation,
    PatchSet,
    PlanFileChange,
    ProjectInfo,
)
from noir.infrastructure.database.repositories import (
    FileManifestRepository,
    ProjectRepository,
    TokenRepository,
)
from noir.infrastructure.filesystem.workspace import ProjectWorkspace


@pytest.fixture
def ui_workspace(tmp_path):
    reset_config()
    cfg = NoirConfig(
        _env_file=None, gemini_api_key="", ai_provider="none", data_dir=str(tmp_path / "data")
    )
    app = create_app(cfg)
    token = secrets.token_urlsafe(24)
    TokenRepository().create(ApiToken(token_hash=hashlib.sha256(token.encode()).hexdigest()))
    project = ProjectRepository().create(ProjectInfo(package_name="app.noir.fixture"))
    ws = ProjectWorkspace(project.id, cfg)
    ws.create()
    (ws.decoded_dir / "label.txt").write_text("before\n")
    FileManifestRepository().save(project.id, 0, ws.build_file_manifest())
    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    yield client, cfg, ws
    client.close()
    reset_config()


def stored_plan_patch(cfg, ws):
    plan = PlanService(cfg).create_plan(
        ChangePlan(
            project_id=ws.project_id,
            workspace_revision=0,
            user_request="Change label",
            network_destinations=["https://example.invalid"],
            file_changes=[
                PlanFileChange(
                    relative_path="label.txt", operation=PatchOperationType.REPLACE_BLOCK
                )
            ],
        )
    )
    patch = PatchService(cfg).store_patch(
        PatchSet(
            provenance=Provenance.MANUAL,
            project_id=ws.project_id,
            plan_id=plan.plan_id,
            workspace_revision=0,
            operations=[
                PatchOperation(
                    relative_path="label.txt",
                    operation=PatchOperationType.REPLACE_BLOCK,
                    match_content="before",
                    new_content="after",
                )
            ],
        )
    )
    return plan, patch


def test_review_recovery_exact_hashes_and_real_diff(ui_workspace):
    client, cfg, ws = ui_workspace
    plan, patch = stored_plan_patch(cfg, ws)
    base = f"/v1/projects/{ws.project_id}"
    detail = client.get(f"{base}/plans/{plan.plan_id}").json()
    assert detail["plan_hash"] == plan.compute_hash()
    assert detail["network_destinations"] == ["https://example.invalid"]
    assert detail["review"] == {"approved": False, "stale": False, "current_revision": 0}
    assert (
        client.post(f"{base}/plans/{plan.plan_id}/approve", json={"hash": "wrong"}).status_code
        == 400
    )
    assert (
        client.post(
            f"{base}/plans/{plan.plan_id}/approve", json={"hash": plan.compute_hash()}
        ).status_code
        == 200
    )
    assert client.get(f"{base}/plans/{plan.plan_id}").json()["review"]["approved"] is True

    preview = client.get(f"{base}/patches/{patch.patch_id}/diff").json()["diff"]
    assert preview == [
        {
            "path": "label.txt",
            "preview": "--- a/label.txt\n+++ b/label.txt\n@@ -1 +1 @@\n-before\n+after\n",
        }
    ]
    assert (ws.decoded_dir / "label.txt").read_text() == "before\n"
    assert client.post(f"{base}/patches/{patch.patch_id}/apply").status_code == 400
    assert (
        client.post(
            f"{base}/patches/{patch.patch_id}/approve", json={"hash": patch.compute_hash()}
        ).status_code
        == 200
    )
    assert client.get(f"{base}/patches/{patch.patch_id}").json()["review"]["approved"] is True
    assert client.post(f"{base}/patches/{patch.patch_id}/apply").status_code == 200
    assert (ws.decoded_dir / "label.txt").read_text() == "after\n"
    detail = client.get(f"{base}/patches/{patch.patch_id}").json()
    assert detail["applied"] is True
    assert detail["review"]["current_revision"] == 1
    assert detail["review"]["approved"] is False
    assert client.get(f"{base}/plans/{plan.plan_id}").json()["review"]["stale"] is True
    report = client.get(f"{base}/audit?format=markdown").json()["markdown"]
    assert plan.plan_id in report and "Approvals" in report
    assert client.post(f"{base}/patches/{patch.patch_id}/undo").status_code == 200
    assert client.get(f"{base}/patches/{patch.patch_id}").json()["applied"] is False
    assert (ws.decoded_dir / "label.txt").read_text() == "before\n"


def test_review_reads_enforce_auth_and_project_ownership(ui_workspace):
    client, cfg, ws = ui_workspace
    plan, patch = stored_plan_patch(cfg, ws)
    other = ProjectRepository().create(ProjectInfo())
    for suffix in [
        f"plans/{plan.plan_id}",
        f"patches/{patch.patch_id}",
        f"patches/{patch.patch_id}/diff",
    ]:
        assert (
            client.get(
                f"/v1/projects/{ws.project_id}/{suffix}", headers={"Authorization": ""}
            ).status_code
            == 401
        )
        assert client.get(f"/v1/projects/{other.id}/{suffix}").status_code in (400, 404)


def test_manual_session_revision_and_file_contract(ui_workspace):
    client, _, ws = ui_workspace
    base = f"/v1/projects/{ws.project_id}"
    assert client.get(f"{base}/files").json() == {"files": ["label.txt"]}
    assert client.get(f"{base}/files/read?path=label.txt").json()["content"] == "before\n"
    body = {"relative_path": "label.txt", "content": "manual\n", "expected_revision": 0}
    assert client.put(f"{base}/files", json=body).status_code == 400
    assert client.post(f"{base}/manual/begin").status_code == 200
    session = client.get(f"{base}/manual/session").json()
    assert session["active"] is True
    assert client.put(f"{base}/files", json={**body, "expected_revision": 99}).status_code == 409
    assert client.put(f"{base}/files", json=body).status_code == 200
    assert client.get(f"{base}/manual/session").json()["active"] is True
    assert client.post(f"{base}/manual/record", json={"message": "UI test"}).status_code == 200
    assert client.get(f"{base}/manual/session").json() == {"active": False}
    assert client.get(base).json()["workspace_revision"] == 1
    report = client.get(f"{base}/audit?format=json").json()
    assert report["manual_sessions"][0]["changed_files"] == ["label.txt"]
    assert report["manual_sessions"][0]["message"] == "UI test"
    assert "UI test" in client.get(f"{base}/audit?format=markdown").json()["markdown"]
    assert client.put(f"{base}/files", json=body).status_code == 409


def test_ai_and_signing_still_require_consent(ui_workspace):
    client, _, ws = ui_workspace
    base = f"/v1/projects/{ws.project_id}"
    assert client.post(f"{base}/plans", json={"user_request": "rename"}).status_code == 400
    assert (
        client.post(f"{base}/sign", json={"build_id": "unknown", "profile": "unknown"}).status_code
        == 400
    )
