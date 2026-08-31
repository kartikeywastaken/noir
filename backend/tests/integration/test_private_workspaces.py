"""Isolation contracts: no cloud accounts, keys, or user APKs are used here."""

import hashlib
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from noir.api.app import create_app
from noir.application.access_service import AccessError, AccessService, _now
from noir.domain.config import NoirConfig, reset_config
from noir.domain.enums import WorkflowStage
from noir.domain.models import (
    ApiToken,
    BuildResult,
    ChangePlan,
    JobInfo,
    PatchOperation,
    PatchSet,
    PlanFileChange,
    ProjectInfo,
    SigningProfile,
)
from noir.infrastructure.database.engine import InviteRow, get_session, init_db
from noir.infrastructure.database.repositories import (
    ApprovalRepository,
    BuildRepository,
    FileManifestRepository,
    JobRepository,
    ProjectRepository,
    SigningProfileRepository,
    TokenRepository,
)
from noir.infrastructure.filesystem.workspace import ProjectWorkspace


def test_three_invites_remain_independent_and_pasted_codes_work(isolated):
    app, _, _ = isolated
    access = AccessService()
    invites = [access.invite(name) for name in ("One", "Two", "Three")]
    client = TestClient(app)
    a, b, c = [item["invite_code"] for item in invites]
    first = client.post("/v1/auth/redeem", json={"code": a})
    assert first.status_code == 200
    assert client.post("/v1/auth/redeem", json={"code": a}).status_code == 400
    with get_session() as session:
        for invitation in invites[1:]:
            row = session.get(InviteRow, invitation["invite_id"])
            assert row.redeemed_at is None and not row.revoked
    # Rich/terminal wrapping and copied JSON are both accepted, without changing case.
    second = client.post("/v1/auth/redeem", json={"code": b[:20] + "\n  " + b[20:]})
    third = client.post("/v1/auth/redeem", json={"code": json.dumps(invites[2])})
    assert second.status_code == third.status_code == 200
    assert len({r.json()["user"]["user_id"] for r in (first, second, third)}) == 3
    wrong = client.post("/v1/auth/redeem", json={"code": invites[1]["user_id"]})
    assert "43-character" in wrong.json()["detail"]


@pytest.mark.e2e
@pytest.mark.skipif(os.getenv("NOIR_RUN_E2E") != "1", reason="Opt-in real APK toolchain")
def test_three_step_real_build_and_retry_signing_without_reapplying(isolated, monkeypatch):
    from noir.application.build_service import BuildService
    from noir.application.demo import _build_fixture_apk
    from noir.application.signing_service import SigningService
    from noir.infrastructure.database.repositories import PatchRepository, PlanRepository

    app, config, users = isolated
    client = users[0][0]
    monkeypatch.setenv("NOIR_KEYSTORE_PASSWORD", "isolated-test-only-password")
    fixture = _build_fixture_apk(config)
    response = client.post(
        "/v1/import?authorized=true", files={"file": ("fixture.apk", fixture.read_bytes())}
    )
    assert response.status_code == 202
    job = JobRepository().get(response.json()["job_id"])
    app.state.queue.execute(job)
    assert JobRepository().get(job.job_id).state == "succeeded"
    project_id = job.project_id

    class Provider:
        def __init__(self, **kwargs):
            pass

        def generate_plan(self, request, analysis, context, *, project_id):
            return ChangePlan(
                project_id=project_id,
                workspace_revision=0,
                user_request=request,
                intended_outcome="Rename app",
                file_changes=[
                    PlanFileChange(
                        relative_path="res/values/strings.xml",
                        operation="replace_block",
                        description="Rename",
                    )
                ],
            )

        def generate_patch(self, plan, context):
            return PatchSet(
                plan_id=plan.plan_id,
                project_id=plan.project_id,
                workspace_revision=0,
                operations=[
                    PatchOperation(
                        relative_path="res/values/strings.xml",
                        operation="replace_block",
                        match_content="NOIR Test",
                        new_content="NOIR Three Step",
                    )
                ],
            )

    monkeypatch.setattr("noir.application.ai_service.GeminiProvider", Provider)
    response = client.post(
        f"/v1/projects/{project_id}/workflow/prepare",
        headers={"Idempotency-Key": "prepare"},
        json={"user_request": "rename", "revision": 0, "allow_ai_upload": True},
    )
    job = JobRepository().get(response.json()["job_id"])
    app.state.queue.execute(job)
    job = JobRepository().get(job.job_id)
    assert job.state == "succeeded", job.error_message
    plan = PlanRepository().get(job.result_data["result"]["plan_id"])
    patch = PatchRepository().get(job.result_data["result"]["patch_id"])
    payload = {
        "plan_id": plan.plan_id,
        "patch_id": patch.patch_id,
        "plan_hash": plan.compute_hash(),
        "patch_hash": patch.compute_hash(),
        "revision": 0,
        "confirm": True,
    }
    real_sign = SigningService.sign

    def failed_sign(*args, **kwargs):
        raise ValueError("Temporary signing failure")

    monkeypatch.setattr(SigningService, "sign", failed_sign)
    response = client.post(
        f"/v1/projects/{project_id}/workflow/finish",
        json=payload,
        headers={"Idempotency-Key": "first"},
    )
    assert response.status_code == 202, response.text
    job = JobRepository().get(response.json()["job_id"])
    app.state.queue.execute(job)
    failed = JobRepository().get(job.job_id)
    assert failed.state == "failed" and "Temporary signing failure" in failed.error_message
    assert failed.result_data["build_id"]
    assert ProjectRepository().get(project_id).workspace_revision == 1
    monkeypatch.setattr(SigningService, "sign", real_sign)

    def unexpected_build(*args, **kwargs):
        pytest.fail("Retry rebuilt an already successful APK")

    monkeypatch.setattr(BuildService, "build", unexpected_build)
    response = client.post(
        f"/v1/projects/{project_id}/workflow/finish",
        json=payload,
        headers={"Idempotency-Key": "retry"},
    )
    assert response.status_code == 202, response.text
    job = JobRepository().get(response.json()["job_id"])
    app.state.queue.execute(job)
    finished = JobRepository().get(job.job_id)
    assert finished.state == "succeeded", finished.error_message
    build_id = finished.result_data["result"]["build_id"]
    assert build_id == failed.result_data["build_id"]
    assert ProjectRepository().get(project_id).workspace_revision == 1
    assert client.get(f"/v1/projects/{project_id}/builds/{build_id}/verify").json()["verified"]
    assert client.get(f"/v1/projects/{project_id}/builds/{build_id}/download").status_code == 200


def test_queued_preview_is_not_approval_and_finish_is_hash_bound(isolated, monkeypatch):
    from noir.application.patch_service import PatchService, PlanServiceError
    from noir.application.workflow_service import check_finish
    from noir.domain.enums import ApprovalScope
    from noir.infrastructure.database.repositories import PatchRepository, PlanRepository

    app, config, users = isolated
    alice, owner, *_ = users[0]
    project, workspace = project_for(config, owner)

    class Provider:
        def __init__(self, **kwargs):
            pass

        def generate_plan(self, request, analysis, context, *, project_id):
            return ChangePlan(
                project_id=project_id,
                workspace_revision=0,
                user_request=request,
                intended_outcome="Rename label",
                file_changes=[
                    PlanFileChange(
                        relative_path="label.txt", operation="replace_block", description="Rename"
                    )
                ],
            )

        def generate_patch(self, plan, context):
            return PatchSet(
                plan_id=plan.plan_id,
                project_id=plan.project_id,
                workspace_revision=0,
                operations=[
                    PatchOperation(
                        relative_path="label.txt",
                        operation="replace_block",
                        match_content="private",
                        new_content="renamed",
                    )
                ],
            )

    monkeypatch.setattr("noir.application.ai_service.GeminiProvider", Provider)
    body = {"user_request": "edit private text", "revision": 0, "allow_ai_upload": True}
    path = f"/v1/projects/{project.id}/workflow/prepare"
    response = alice.post(path, json=body, headers={"Idempotency-Key": "same"})
    assert response.status_code == 202
    duplicate = alice.post(path, json=body, headers={"Idempotency-Key": "same"})
    assert duplicate.json()["job_id"] == response.json()["job_id"]
    assert alice.post(path, json=body, headers={"Idempotency-Key": "different"}).status_code == 409
    job = JobRepository().get(response.json()["job_id"])
    app.state.queue.execute(job)
    job = JobRepository().get(job.job_id)
    assert job.state == "succeeded", job.error_message
    plan = PlanRepository().get(job.result_data["result"]["plan_id"])
    patch = PatchRepository().get(job.result_data["result"]["patch_id"])
    assert not ApprovalRepository().find_valid(
        project.id, ApprovalScope.PLAN, plan.compute_hash(), 0
    )
    assert (workspace.decoded_dir / "label.txt").read_text() == "private\n"
    with pytest.raises(PlanServiceError, match="Plan approval"):
        PatchService(config).apply_patch(project.id, patch.patch_id)
    finish = {
        "plan_id": plan.plan_id,
        "patch_id": patch.patch_id,
        "revision": 0,
        "plan_hash": plan.compute_hash(),
        "patch_hash": patch.compute_hash(),
        "confirm": True,
    }
    endpoint = f"/v1/projects/{project.id}/workflow/finish"
    assert (
        users[1][0].post(endpoint, json=finish, headers={"Idempotency-Key": "finish"}).status_code
        == 404
    )
    assert (
        alice.post(
            endpoint, json={**finish, "confirm": False}, headers={"Idempotency-Key": "f"}
        ).status_code
        == 409
    )
    assert (
        alice.post(
            endpoint, json={**finish, "patch_hash": "0" * 64}, headers={"Idempotency-Key": "f"}
        ).status_code
        == 409
    )
    assert check_finish(config, project.id, finish)[2] is False
    accepted = alice.post(endpoint, json=finish, headers={"Idempotency-Key": "finish"})
    assert accepted.status_code == 202
    # Cancellation before execution applies no edits and creates no approval.
    queued = JobRepository().get(accepted.json()["job_id"])
    JobRepository().request_cancel(queued.job_id)
    app.state.queue.execute(queued)
    assert JobRepository().get(queued.job_id).state == "cancelled"
    assert (workspace.decoded_dir / "label.txt").read_text() == "private\n"


def test_unsupported_prepare_stops_before_patch_generation(isolated, monkeypatch):
    from noir.infrastructure.database.repositories import PatchRepository, PlanRepository

    app, config, users = isolated
    alice, owner, *_ = users[0]
    project, _ = project_for(config, owner)

    class Provider:
        def __init__(self, **kwargs):
            pass

        def generate_plan(self, request, analysis, context, *, project_id):
            return ChangePlan(
                project_id=project_id,
                workspace_revision=0,
                user_request=request,
                intended_outcome="No safe implementation point was identified",
                file_changes=[],
                unsupported_aspects=["The requested behavior is not present in supplied context"],
            )

        def generate_patch(self, plan, context):
            pytest.fail("Unsupported plan reached patch generation")

    monkeypatch.setattr("noir.application.ai_service.GeminiProvider", Provider)
    response = alice.post(
        f"/v1/projects/{project.id}/workflow/prepare",
        json={
            "user_request": "change unsupported engine rules",
            "revision": 0,
            "allow_ai_upload": True,
        },
        headers={"Idempotency-Key": "unsupported"},
    )
    assert response.status_code == 202
    queued = JobRepository().get(response.json()["job_id"])
    app.state.queue.execute(queued)
    completed = JobRepository().get(queued.job_id)

    assert completed.state == "succeeded", completed.error_message
    result = completed.result_data["result"]
    assert result["unsupported"] is True
    assert "patch_id" not in result
    assert PlanRepository().get(result["plan_id"]).file_changes == []
    assert PatchRepository().list_by_project(project.id) == []


@pytest.fixture
def isolated(tmp_path):
    reset_config()
    config = NoirConfig(
        _env_file=None, gemini_api_key="", ai_provider="none", data_dir=str(tmp_path / "data")
    )
    app = create_app(config)
    access = AccessService()
    users = []
    for name in ("Alice", "Bob"):
        invite = access.invite(name)
        client = TestClient(app)
        response = client.post("/v1/auth/redeem", json={"code": invite["invite_code"]})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        data = response.json()
        client.headers["Authorization"] = f"Bearer {data['token']}"
        users.append((client, data["user"]["user_id"], data["token"], invite))
    yield app, config, users
    for client, *_ in users:
        client.close()
    reset_config()


def project_for(config, owner):
    project = ProjectInfo(
        id=uuid4().hex[:16], original_filename=f"{owner}.apk", package_name="app.noir.fixture"
    )
    AccessService().claim_project(owner, project.id)
    ProjectRepository().create(project)
    workspace = ProjectWorkspace(project.id, config)
    workspace.create()
    (workspace.decoded_dir / "label.txt").write_text("private\n")
    FileManifestRepository().save(project.id, 0, workspace.build_file_manifest())
    return project, workspace


def test_every_project_route_is_guarded_before_read_or_mutation(isolated):
    app, config, users = isolated
    alice, owner, *_ = users[0]
    bob = users[1][0]
    project, workspace = project_for(config, owner)
    body = {
        "user_request": "test",
        "allow_ai_upload": True,
        "hash": "a" * 64,
        "relative_path": "label.txt",
        "content": "changed",
        "expected_revision": 0,
        "message": "test",
        "build_id": "build",
        "profile": "profile",
        "confirm": True,
    }
    checked = 0
    for route in app.routes:
        if not isinstance(route, APIRoute) or "/projects/{project_id}" not in route.path:
            continue
        path = route.path.replace("{project_id}", project.id)
        for name in ("plan_id", "patch_id", "build_id"):
            path = path.replace("{" + name + "}", name)
        for method in route.methods:
            response = bob.request(
                method,
                path,
                params={"path": "label.txt", "q": "private", "plan_id": "plan"},
                json=body,
            )
            assert response.status_code == 404, (method, path, response.text)
            assert "private" not in response.text
            checked += 1
    assert checked >= 27
    assert (
        alice.get(f"/v1/projects/{project.id}/files/read?path=label.txt").json()["content"]
        == "private\n"
    )
    assert (workspace.decoded_dir / "label.txt").read_text() == "private\n"


def test_lists_history_and_profile_names_are_owner_scoped(isolated):
    _, config, users = isolated
    projects = []
    for _client, owner, *_ in users:
        project, workspace = project_for(config, owner)
        projects.append(project)
        BuildRepository().create(
            BuildResult(
                project_id=project.id,
                workspace_revision=0,
                success=True,
                signed_apk_path="/private/server/path.apk",
                signed_apk_hash="a" * 64,
            )
        )
        BuildRepository().create(
            BuildResult(
                project_id=project.id,
                workspace_revision=0,
                success=False,
                error_message="Fixture build failure",
            )
        )
        SigningProfileRepository().create(
            SigningProfile(name=f"key-{owner}", profile_type="user_supplied"), user_id=owner
        )
        JobRepository().create(JobInfo(project_id=project.id, stage=WorkflowStage.REBUILDING))
    for (client, owner, *_), project in zip(users, projects, strict=True):
        assert [p["id"] for p in client.get("/v1/projects").json()["projects"]] == [project.id]
        assert {j["project_id"] for j in client.get("/v1/jobs").json()["jobs"]} == {project.id}
        history = client.get("/v1/history?limit=1").json()
        assert history["total"] == 2 and len(history["builds"]) == 1
        assert history["builds"][0]["project_id"] == project.id
        assert "signed_apk_path" not in history["builds"][0]
        assert len(client.get("/v1/history?offset=1&limit=1").json()["builds"]) == 1
        assert client.get("/v1/keys").json()["profiles"] == [
            {"name": f"key-{owner}", "type": "user_supplied"}
        ]
        assert client.get("/v1/auth/me").json()["user_id"] == owner
    assert users[0][0].get("/v1/history?offset=-1").status_code == 422


def test_queued_import_jobs_idempotency_and_cancel_are_private(isolated):
    _, _, users = isolated
    alice, alice_id, *_ = users[0]
    bob, bob_id, *_ = users[1]
    payload = b"unit-test-upload-not-executed-by-worker"

    def submit(client):
        return client.post(
            "/v1/import?authorized=true",
            headers={"Idempotency-Key": "same-client-key"},
            files={"file": ("fixture.apk", payload)},
        )

    first, second = submit(alice), submit(bob)
    assert first.status_code == second.status_code == 202
    a, b = first.json(), second.json()
    assert a["job_id"] != b["job_id"]
    assert submit(alice).json()["job_id"] == a["job_id"]
    assert submit(bob).json()["job_id"] == b["job_id"]
    assert AccessService().owns_project(alice_id, a["project_id"])
    assert AccessService().owns_project(bob_id, b["project_id"])
    for suffix in ("", "/events", "/cancel"):
        method = "POST" if suffix == "/cancel" else "GET"
        assert bob.request(method, f"/v1/jobs/{a['job_id']}{suffix}").status_code == 404
    assert bob.get(f"/v1/jobs?project_id={a['project_id']}").status_code == 404
    assert alice.post(f"/v1/jobs/{a['job_id']}/cancel").status_code == 200
    assert "event: done" in alice.get(f"/v1/jobs/{a['job_id']}/events").text


def test_foreign_signer_and_forged_owner_input_rejected(isolated):
    _, config, users = isolated
    alice, owner, *_ = users[0]
    bob, other_owner, *_ = users[1]
    project, _ = project_for(config, owner)
    SigningProfileRepository().create(
        SigningProfile(name="bob-key", profile_type="user_supplied"), user_id=other_owner
    )
    assert (
        alice.post(
            f"/v1/projects/{project.id}/sign",
            json={"build_id": "x", "profile": "bob-key", "confirm": True},
        ).status_code
        == 404
    )
    assert (
        bob.get(
            f"/v1/projects/{project.id}", params={"user_id": owner}, headers={"X-User-ID": owner}
        ).status_code
        == 404
    )
    uploaded = bob.post(
        f"/v1/import?authorized=true&user_id={owner}", files={"file": ("unit.apk", b"test")}
    ).json()
    assert AccessService().owns_project(other_owner, uploaded["project_id"])


def test_foreign_artifact_cannot_be_reparented_into_an_owned_project(isolated):
    _, config, users = isolated
    alice, owner, *_ = users[0]
    bob, other, *_ = users[1]
    own_project, _ = project_for(config, owner)
    foreign_project, workspace = project_for(config, other)
    artifact = workspace.root / "unit-artifact.bin"
    artifact.write_bytes(b"Bob's private unit-test artifact")
    build = BuildRepository().create(
        BuildResult(
            project_id=foreign_project.id,
            workspace_revision=0,
            success=True,
            unsigned_apk_path=str(artifact),
            unsigned_apk_hash=hashlib.sha256(artifact.read_bytes()).hexdigest(),
        )
    )
    assert (
        bob.get(
            f"/v1/projects/{foreign_project.id}/builds/{build.build_id}/download?artifact=unsigned"
        ).content
        == artifact.read_bytes()
    )
    for project_id in (foreign_project.id, own_project.id):
        for suffix in ("download?artifact=unsigned", "verify"):
            assert (
                alice.get(f"/v1/projects/{project_id}/builds/{build.build_id}/{suffix}").status_code
                == 404
            )


def test_all_private_routes_have_the_authentication_dependency(isolated):
    from noir.api.app import _verify_token

    app, _, _ = isolated
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in {"/v1/health", "/v1/auth/redeem"}:
            continue
        assert any(
            dependency.call is _verify_token for dependency in route.dependant.dependencies
        ), route.path


def test_invite_replay_expiry_revocation_and_same_workspace_new_device(isolated):
    _, _, users = isolated
    alice, owner, _, invite = users[0]
    access = AccessService()
    assert alice.post("/v1/auth/redeem", json={"code": invite["invite_code"]}).status_code == 400
    extra = access.invite("Ignored", user_id=owner)
    response = alice.post("/v1/auth/redeem", json={"code": extra["invite_code"]}).json()
    assert response["user"]["user_id"] == owner and response["token"] != users[0][2]
    expired = access.invite("Expired")
    with get_session() as session:
        session.get(InviteRow, expired["invite_id"]).expires_at = _now() - timedelta(seconds=1)
        session.commit()
    assert alice.post("/v1/auth/redeem", json={"code": expired["invite_code"]}).status_code == 400
    unused = access.invite("Alice", user_id=owner)
    access.revoke_user(owner)
    assert alice.get("/v1/projects").status_code == 401
    assert alice.post("/v1/auth/redeem", json={"code": unused["invite_code"]}).status_code == 400
    with pytest.raises(AccessError):
        access.revoke_user("local")


def test_single_use_invite_is_atomic_and_logout_revokes_only_one_device(isolated):
    app, _, users = isolated
    owner = users[0][1]
    invite = AccessService().invite("Alice", user_id=owner)

    def redeem(_):
        with TestClient(app) as client:
            return client.post("/v1/auth/redeem", json={"code": invite["invite_code"]})

    # No lifespan: this is an invitation test, not a second queue-worker process.
    def request(_):
        client = TestClient(app)
        try:
            return client.post("/v1/auth/redeem", json={"code": invite["invite_code"]})
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(request, range(2)))
    assert sorted(r.status_code for r in responses) == [200, 400]
    assert users[0][0].post("/v1/auth/logout").status_code == 200
    assert users[0][0].get("/v1/projects").status_code == 401
    token = next(r.json()["token"] for r in responses if r.status_code == 200)
    assert (
        users[0][0].get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )


def test_legacy_database_migration_preserves_owner_data_and_token(tmp_path):
    reset_config()
    config = NoirConfig(
        _env_file=None, data_dir=str(tmp_path / "legacy"), ai_provider="none", gemini_api_key=""
    )
    app = create_app(config)
    raw = "local-owner-test-token"
    token = ApiToken(token_hash=hashlib.sha256(raw.encode()).hexdigest())
    TokenRepository().create(token)
    project = ProjectRepository().create(ProjectInfo())
    # Recreate an actual pre-migration database by dropping only the new tables
    # inside this isolated fixture, then initialize it through a different DB.
    with sqlite3.connect(tmp_path / "legacy/noir.db") as db:
        for name in (
            "token_access",
            "project_access",
            "signing_access",
            "workspace_invites",
            "workspace_users",
        ):
            db.execute(f"DROP TABLE {name}")
    init_db(f"sqlite:///{tmp_path / 'other.db'}")
    app = create_app(config)
    client = TestClient(app, headers={"Authorization": f"Bearer {raw}"})
    assert client.get("/v1/auth/me").json()["user_id"] == "local"
    assert client.get("/v1/projects").json()["projects"][0]["id"] == project.id
    invited = AccessService().redeem(AccessService().invite("New user")["invite_code"])
    assert (
        client.get("/v1/projects", headers={"Authorization": f"Bearer {invited['token']}"}).json()[
            "projects"
        ]
        == []
    )
    client.close()
    reset_config()
