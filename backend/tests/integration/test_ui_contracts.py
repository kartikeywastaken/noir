"""UI-facing API contracts, exercised with isolated workspaces and no AI calls."""

import hashlib
import json
import secrets
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from noir.api.app import create_app
from noir.application.patch_service import PatchService, PlanService, PlanServiceError
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
from noir.infrastructure.ai.gemini import GeminiProvider
from noir.infrastructure.database.repositories import (
    FileManifestRepository,
    ProjectRepository,
    TokenRepository,
)
from noir.infrastructure.filesystem.workspace import ProjectWorkspace
from noir.patches.engine import PatchValidationError


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


def test_patch_must_cover_every_approved_plan_binding(ui_workspace):
    _, cfg, ws = ui_workspace
    second = ws.decoded_dir / "second.txt"
    second.write_text("before\n")
    plan = PlanService(cfg).create_plan(
        ChangePlan(
            project_id=ws.project_id,
            workspace_revision=0,
            user_request="Change both files",
            file_changes=[
                PlanFileChange(
                    relative_path="label.txt", operation=PatchOperationType.REPLACE_BLOCK
                ),
                PlanFileChange(
                    relative_path="second.txt", operation=PatchOperationType.REPLACE_BLOCK
                ),
            ],
        )
    )

    with pytest.raises(PlanServiceError, match="does not implement every approved plan change"):
        PatchService(cfg).store_patch(
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


def test_patch_rejects_approved_noop_operation(ui_workspace):
    _, cfg, ws = ui_workspace
    plan = PlanService(cfg).create_plan(
        ChangePlan(
            project_id=ws.project_id,
            workspace_revision=0,
            user_request="Change label",
            file_changes=[
                PlanFileChange(
                    relative_path="label.txt", operation=PatchOperationType.REPLACE_BLOCK
                )
            ],
        )
    )

    with pytest.raises(PatchValidationError, match="produces no change"):
        PatchService(cfg).store_patch(
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
                        new_content="before",
                    )
                ],
            )
        )


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


def test_generate_patch_with_large_smali_and_new_method(ui_workspace, monkeypatch):
    """Exercise the complete HTTP workflow; only the remote SDK boundary is a test double."""
    client, cfg, _ = ui_workspace
    project = ProjectRepository().create(ProjectInfo(package_name="app.noir.largecontext"))
    ws = ProjectWorkspace(project.id, cfg)
    ws.create()
    cfg.ai_provider = "gemini"
    from pydantic import SecretStr

    cfg.gemini_api_key = SecretStr("test-only")
    path = ws.decoded_dir / "smali/Example.smali"
    path.parent.mkdir()
    source = (
        ".class public LExample;\n.super Landroid/app/Activity;\n"
        + "# metadata\n" * 7400
        + "# virtual methods\n"
    )
    assert 80_000 < len(source.encode()) < 90_000
    path.write_text(source)
    FileManifestRepository().save(ws.project_id, 0, ws.build_file_manifest())
    plan = PlanService(cfg).create_plan(
        ChangePlan(
            project_id=ws.project_id,
            workspace_revision=0,
            user_request="Add a method to the class",
            file_changes=[
                PlanFileChange(
                    relative_path="smali/Example.smali",
                    operation=PatchOperationType.SMALI_INSERT_AT_ANCHOR,
                )
            ],
        )
    )
    calls = []

    def generate_content(**kwargs):
        calls.append(kwargs)
        prompt = kwargs["contents"]
        sdk_config = kwargs["config"]
        schema = sdk_config.response_json_schema
        if schema is None:
            schema = json.loads(prompt.rsplit("\n\nREQUIRED JSON SCHEMA:\n", 1)[1])
        assert json.dumps(source, ensure_ascii=False) in prompt
        assert "class-level" in prompt
        assert set(schema["properties"]["operations"]["items"]["required"]) >= {
            "class_descriptor",
            "method_signature",
            "anchor",
            "new_content",
        }
        assert (
            len(prompt.encode())
            + len(sdk_config.system_instruction.encode())
            + (
                0
                if sdk_config.response_json_schema is None
                else len(json.dumps(schema, separators=(",", ":")).encode())
            )
        ) <= cfg.ai_max_request_size
        return SimpleNamespace(
            prompt_feedback=None,
            candidates=[SimpleNamespace(finish_reason="STOP")],
            text=json.dumps(
                {
                    "operations": [
                        {
                            "relative_path": "smali/Example.smali",
                            "operation": "smali_insert_at_anchor",
                            "class_descriptor": "LExample;",
                            "method_signature": "added()V",
                            "anchor": "# virtual methods",
                            "new_content": (
                                ".method public added()V\n    .locals 0\n"
                                "    return-void\n.end method"
                            ),
                        }
                    ]
                }
            ),
        )

    monkeypatch.setattr(
        GeminiProvider,
        "_get_client",
        lambda self: SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)),
    )
    base = f"/v1/projects/{ws.project_id}"
    url = f"{base}/patches?plan_id={plan.plan_id}"
    assert client.post(url).status_code == 400
    assert not calls  # The larger context must not bypass plan approval.
    assert (
        client.post(
            f"{base}/plans/{plan.plan_id}/approve", json={"hash": plan.compute_hash()}
        ).status_code
        == 200
    )
    response = client.post(url)
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    patch_id = response.json()["patch_id"]
    diff = client.get(f"{base}/patches/{patch_id}/diff").json()["diff"]
    assert "+.method public added()V" in diff[0]["preview"]
    assert client.get(f"{base}/patches/{patch_id}").json()["review"]["approved"] is False
    assert client.post(f"{base}/patches/{patch_id}/apply").status_code == 400
    assert path.read_text() == source
    assert client.get(base).json()["workspace_revision"] == 0
