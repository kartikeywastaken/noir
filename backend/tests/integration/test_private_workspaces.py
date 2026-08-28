"""Isolation contracts: no cloud accounts, keys, or user APKs are used here."""

import hashlib
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
from noir.domain.models import ApiToken, BuildResult, JobInfo, ProjectInfo, SigningProfile
from noir.infrastructure.database.engine import InviteRow, get_session, init_db
from noir.infrastructure.database.repositories import (
    BuildRepository,
    FileManifestRepository,
    JobRepository,
    ProjectRepository,
    SigningProfileRepository,
    TokenRepository,
)
from noir.infrastructure.filesystem.workspace import ProjectWorkspace


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
