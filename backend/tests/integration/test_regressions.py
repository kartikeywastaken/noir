"""Regression tests. Temporary inputs are test fixtures, never runtime fallbacks."""

import hashlib
import json
import os
import secrets
import sys
import threading
import time
import zipfile
from types import SimpleNamespace

import pytest

from noir.domain.config import NoirConfig, reset_config
from noir.domain.enums import JobState, PatchOperationType, WorkflowStage
from noir.domain.models import JobInfo, PatchOperation, PatchSet, ProjectInfo
from noir.infrastructure.database.engine import init_db
from noir.infrastructure.database.repositories import (
    FileManifestRepository,
    JobRepository,
    ProjectRepository,
)
from noir.infrastructure.filesystem.workspace import (
    PathSecurityError,
    ProjectWorkspace,
    compute_file_hash,
    safe_resolve,
)
from noir.patches.engine import PatchEngine, PatchError


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("NOIR_DATA_DIR", str(tmp_path / "data"))
    reset_config()
    cfg = NoirConfig(_env_file=None, gemini_api_key="", data_dir=str(tmp_path / "data"))
    cfg.ensure_directories()
    init_db(cfg.effective_database_url)
    project = ProjectInfo(package_name="com.noir.regression")
    ProjectRepository().create(project)
    ws = ProjectWorkspace(project.id, cfg)
    ws.create()
    (ws.decoded_dir / "sample.txt").write_text("before\n")
    FileManifestRepository().save(project.id, 0, ws.build_file_manifest())
    yield cfg, ws
    reset_config()


def patch_for(ws, *ops):
    return PatchSet(
        project_id=ws.project_id, plan_id="testplan", workspace_revision=0, operations=list(ops)
    )


def test_plan_generation_regrounds_an_invented_path_before_saving(workspace, monkeypatch):
    from noir.application.ai_service import generate_plan
    from noir.domain.models import AnalysisResult, ChangePlan, PlanFileChange
    from noir.infrastructure.database.repositories import PlanRepository

    cfg, ws = workspace

    class Provider:
        calls = 0

        def __init__(self, **kwargs):
            pass

        def generate_plan(self, request, analysis, context, *, project_id):
            self.__class__.calls += 1
            if self.calls == 1:
                path = "assets/public/js/app.js"
                assert not context.get("planning_feedback")
            else:
                path = "sample.txt"
                assert "assets/public/js/app.js" in context["planning_feedback"]
            return ChangePlan(
                project_id=project_id,
                workspace_revision=0,
                user_request=request,
                file_changes=[
                    PlanFileChange(
                        relative_path=path,
                        operation=PatchOperationType.REPLACE_BLOCK,
                    )
                ],
                intended_outcome="Use a real decoded path",
            )

    monkeypatch.setattr("noir.application.ai_service.GeminiProvider", Provider)
    monkeypatch.setattr(
        "noir.application.ai_service.AnalysisService.analyze",
        lambda *args, **kwargs: AnalysisResult(project_id=ws.project_id),
    )

    plan = generate_plan(cfg, ws.project_id, "make a small change", True)

    assert Provider.calls == 2
    assert plan.file_changes[0].relative_path == "sample.txt"
    assert len(PlanRepository().list_by_project(ws.project_id)) == 1


def test_plan_accepts_real_binary_selected_outside_truncated_inventory(workspace, monkeypatch):
    from noir.application.ai_service import generate_plan
    from noir.domain.models import AnalysisResult, ChangePlan, PlanFileChange

    cfg, ws = workspace
    relative = "assets/bin/Data/Managed/Assembly-CSharp.dll"
    target = ws.decoded_dir / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(b"MZ test assembly")
    monkeypatch.setattr(
        "noir.application.ai_service.require_clean_workspace", lambda *args, **kwargs: None
    )

    class Provider:
        calls = 0

        def __init__(self, **kwargs):
            pass

        def generate_plan(self, request, analysis, context, *, project_id):
            self.__class__.calls += 1
            return ChangePlan(
                project_id=project_id,
                workspace_revision=0,
                user_request=request,
                file_changes=[
                    PlanFileChange(
                        relative_path=relative,
                        operation=PatchOperationType.CIL_REPLACE_METHOD_BODY,
                    )
                ],
                intended_outcome="Patch a host-inspected managed assembly",
            )

    monkeypatch.setattr("noir.application.ai_service.GeminiProvider", Provider)
    monkeypatch.setattr(
        "noir.application.ai_service.AnalysisService.analyze",
        lambda *args, **kwargs: AnalysisResult(
            project_id=ws.project_id,
            runtime="mono",
            managed_assemblies=[relative],
        ),
    )
    monkeypatch.setattr(
        "noir.application.ai_service.AiContextTools.build_context",
        lambda *args, **kwargs: {
            "files": ["sample.txt"],
            "file_snippets": {},
            "binary_inspection": {relative: {"format": "cil"}},
        },
    )

    plan = generate_plan(cfg, ws.project_id, "change managed game state", True)

    assert Provider.calls == 1
    assert plan.file_changes[0].relative_path == relative


def test_sibling_prefix_symlink_rejected(workspace):
    _, ws = workspace
    outside = ws.root / "decoded_outside"
    outside.mkdir()
    (ws.decoded_dir / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathSecurityError):
        safe_resolve(ws.decoded_dir, "link/secret.txt")


@pytest.mark.parametrize("project_id", ["../escape", "/absolute", "a/b", ".."])
def test_project_id_cannot_escape(workspace, project_id):
    cfg, _ = workspace
    with pytest.raises(PathSecurityError):
        ProjectWorkspace(project_id, cfg)


def test_patch_apply_undo_retains_original_bytes(workspace):
    _, ws = workspace
    path = ws.decoded_dir / "sample.txt"
    original = path.read_bytes()
    engine = PatchEngine(ws)
    patch = patch_for(
        ws,
        PatchOperation(
            relative_path="sample.txt",
            operation=PatchOperationType.REPLACE_BLOCK,
            match_content="before",
            new_content="after",
            expected_preimage_hash=compute_file_hash(path),
        ),
        PatchOperation(
            relative_path="new.txt",
            operation=PatchOperationType.CREATE_FILE,
            new_content="created",
        ),
    )
    engine.apply_patch(patch)
    assert path.read_text() == "after\n"
    engine.undo_patch(patch)
    assert path.read_bytes() == original
    assert not (ws.decoded_dir / "new.txt").exists()


def test_undo_does_not_overwrite_later_edits(workspace):
    _, ws = workspace
    engine = PatchEngine(ws)
    patch = patch_for(
        ws,
        PatchOperation(
            relative_path="sample.txt",
            operation=PatchOperationType.REPLACE_FILE,
            new_content="after",
        ),
    )
    engine.apply_patch(patch)
    (ws.decoded_dir / "sample.txt").write_text("later user edit")
    with pytest.raises(PatchError, match="subsequent"):
        engine.undo_patch(patch)
    assert (ws.decoded_dir / "sample.txt").read_text() == "later user edit"


def test_invalid_later_operation_leaves_all_files_unchanged(workspace):
    _, ws = workspace
    patch = patch_for(
        ws,
        PatchOperation(
            relative_path="sample.txt",
            operation=PatchOperationType.REPLACE_FILE,
            new_content="after",
        ),
        PatchOperation(
            relative_path="missing.txt",
            operation=PatchOperationType.DELETE_FILE,
        ),
    )
    with pytest.raises(PatchError):
        PatchEngine(ws).apply_patch(patch)
    assert (ws.decoded_dir / "sample.txt").read_text() == "before\n"


def test_multiple_operations_same_file_use_original_preimage(workspace):
    _, ws = workspace
    digest = compute_file_hash(ws.decoded_dir / "sample.txt")
    patch = patch_for(
        ws,
        *[
            PatchOperation(
                relative_path="sample.txt",
                operation=PatchOperationType.REPLACE_FILE,
                new_content=text,
                expected_preimage_hash=digest,
            )
            for text in ("middle", "after")
        ],
    )
    PatchEngine(ws).apply_patch(patch)
    assert (ws.decoded_dir / "sample.txt").read_text() == "after"


def test_manifest_preview_and_undo_are_real_xml_changes(workspace):
    _, ws = workspace
    path = ws.decoded_dir / "AndroidManifest.xml"
    original = "<manifest><application /></manifest>"
    path.write_text(original)
    patch = patch_for(
        ws,
        PatchOperation(
            relative_path="AndroidManifest.xml",
            operation=PatchOperationType.MANIFEST_UPDATE,
            xml_element="application",
            xml_attributes={"android:label": "NOIR"},
        ),
    )
    engine = PatchEngine(ws)
    assert "NOIR" in engine.generate_diff(patch)[0]["preview"]
    engine.apply_patch(patch)
    assert "NOIR" in path.read_text()
    engine.undo_patch(patch)
    assert path.read_text() == original


def test_smali_exact_method_replace_and_insert(workspace):
    _, ws = workspace
    path = ws.decoded_dir / "A.smali"
    source = ".class public Lcom/noir/A;\n.super Ljava/lang/Object;\n"
    source += ".method public greet()V\n    .locals 0\n    return-void\n.end method\n"
    path.write_text(source)
    patch = patch_for(
        ws,
        PatchOperation(
            relative_path="A.smali",
            operation=PatchOperationType.SMALI_REPLACE_METHOD,
            class_descriptor="Lcom/noir/A;",
            method_signature="greet()V",
            new_content=".method public greet()V\n    .locals 1\n    return-void\n.end method",
        ),
    )
    engine = PatchEngine(ws)
    engine.apply_patch(patch)
    assert ".locals 1" in path.read_text()
    insert = patch_for(
        ws,
        PatchOperation(
            relative_path="A.smali",
            operation=PatchOperationType.SMALI_INSERT_AT_ANCHOR,
            class_descriptor="Lcom/noir/A;",
            method_signature="greet()V",
            anchor="    .locals 1",
            new_content="    const/4 v0, 0x0",
        ),
    )
    engine.apply_patch(insert)
    assert ".locals 1\n    const/4 v0, 0x0" in path.read_text()


def smali_new_method_patch(ws, **overrides):
    fields = {
        "relative_path": "A.smali",
        "operation": PatchOperationType.SMALI_INSERT_AT_ANCHOR,
        "class_descriptor": "Lcom/noir/A;",
        "method_signature": "added()V",
        "anchor": "# virtual methods",
        "new_content": ".method public added()V\n    .locals 0\n    return-void\n.end method",
    }
    return patch_for(ws, PatchOperation(**{**fields, **overrides}))


def test_smali_new_method_preview_apply_and_undo(workspace):
    _, ws = workspace
    path = ws.decoded_dir / "A.smali"
    original = (
        ".class public Lcom/noir/A;\n.super Ljava/lang/Object;\n# virtual methods\n"
        ".method public existing()V\n    .locals 0\n    return-void\n.end method\n"
    )
    path.write_text(original)
    patch = smali_new_method_patch(ws, expected_preimage_hash=compute_file_hash(path))
    engine = PatchEngine(ws)
    assert "+.method public added()V" in engine.generate_diff(patch)[0]["preview"]
    assert path.read_text() == original
    engine.apply_patch(patch)
    assert path.read_text().count(".method public added()V") == 1
    assert (
        ".method public existing()V\n    .locals 0\n    return-void\n.end method"
        in path.read_text()
    )
    engine.undo_patch(patch)
    assert path.read_text() == original


@pytest.mark.parametrize(
    "source, overrides",
    [
        ("# virtual methods\n", {"class_descriptor": "LWrong;"}),
        ("# virtual methods\n# virtual methods\n", {}),
        ("# virtual methods\n", {"anchor": "missing"}),
        ("# virtual methods\n", {"method_signature": "different()V"}),
        ("# virtual methods\n", {"new_content": ".method public added()V\n    .locals 0"}),
        ("# virtual methods\n", {"new_content": "    const/4 v0, 0x0"}),
        (
            "# virtual methods\n",
            {"new_content": ".end method\n.method public added()V\n.end method"},
        ),
        (
            "# virtual methods\n",
            {
                "new_content": (
                    ".method public added()V\n.method public nested()V\n.end method\n.end method"
                )
            },
        ),
        (
            "# virtual methods\n",
            {
                "new_content": (
                    ".method public added()V\n.end method\n.method public extra()V\n.end method"
                )
            },
        ),
        (
            "# virtual methods\n",
            {"new_content": ".field static surprise:I\n.method public added()V\n.end method"},
        ),
        ("# virtual methods\n.method public added()V\n.locals 0\nreturn-void\n.end method\n", {}),
        (
            ".method public existing()V\n.locals 0\n# virtual methods\nreturn-void\n.end method\n",
            {},
        ),
        (".annotation runtime LExample;\n# virtual methods\n.end annotation\n", {}),
    ],
)
def test_smali_new_method_rejects_unsafe_or_malformed_insertions(workspace, source, overrides):
    _, ws = workspace
    path = ws.decoded_dir / "A.smali"
    original = ".class public Lcom/noir/A;\n.super Ljava/lang/Object;\n" + source
    path.write_text(original)
    with pytest.raises(PatchError):
        PatchEngine(ws).generate_diff(smali_new_method_patch(ws, **overrides))
    assert path.read_text() == original


def test_existing_smali_insertion_cannot_escape_method(workspace):
    _, ws = workspace
    path = ws.decoded_dir / "A.smali"
    original = ".class public Lcom/noir/A;\n.method public existing()V\nreturn-void\n.end method\n"
    path.write_text(original)
    patch = smali_new_method_patch(
        ws, method_signature="existing()V", anchor=".end method", new_content="return-void"
    )
    with pytest.raises(PatchError, match="not inside"):
        PatchEngine(ws).generate_diff(patch)
    assert path.read_text() == original


def test_xml_entities_rejected():
    from xml.etree.ElementTree import ParseError

    from noir.security.xml import fromstring

    with pytest.raises(ParseError):
        fromstring('<!DOCTYPE x [<!ENTITY x "expanded">]><x>&x;</x>')


def test_env_file_load_and_redaction(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("NOIR_GEMINI_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=unit-test-not-a-real-key\n")
    cfg = NoirConfig(_env_file=env_file)
    assert cfg.gemini_api_key.get_secret_value() == "unit-test-not-a-real-key"
    assert "unit-test-not-a-real-key" not in json.dumps(cfg.to_safe_dict())
    assert "unit-test-not-a-real-key" not in cfg.model_dump_json()
    monkeypatch.setenv("GEMINI_API_KEY", "environment-wins")
    assert NoirConfig(_env_file=env_file).gemini_api_key.get_secret_value() == "environment-wins"


def test_no_key_has_no_fake_provider_fallback():
    from noir.infrastructure.ai.gemini import GeminiProvider, GeminiProviderError

    with pytest.raises(GeminiProviderError, match="not configured"):
        GeminiProvider(config=NoirConfig(_env_file=None, gemini_api_key=""))


def test_real_process_cancellation_and_streaming(workspace):
    from noir.application.jobs import job_runtime
    from noir.infrastructure.database.repositories import EventRepository
    from noir.infrastructure.processes.runner import run_tool

    _, ws = workspace
    job = JobInfo(project_id=ws.project_id, stage=WorkflowStage.REBUILDING, state=JobState.RUNNING)
    JobRepository().create(job)
    timer = threading.Timer(0.3, lambda: JobRepository().request_cancel(job.job_id))
    timer.start()
    started = time.monotonic()
    try:
        with job_runtime(job.job_id):
            result = run_tool(
                [
                    sys.executable,
                    "-u",
                    "-c",
                    "import time; print('real process started'); time.sleep(20)",
                ],
                timeout=10,
            )
    finally:
        timer.join()
    assert result.cancelled and result.exit_code != 0
    assert time.monotonic() - started < 5
    assert any(
        "real process started" in e.message for e in EventRepository().list_by_job(job.job_id)
    )


def test_process_output_is_batched_into_bounded_events(workspace):
    from noir.application.jobs import job_runtime
    from noir.infrastructure.database.repositories import EventRepository
    from noir.infrastructure.processes.runner import run_tool

    _, ws = workspace
    job = JobInfo(project_id=ws.project_id, stage=WorkflowStage.DECODING, state=JobState.RUNNING)
    JobRepository().create(job)
    with job_runtime(job.job_id):
        result = run_tool([sys.executable, "-u", "-c", "[print(f'line-{i}') for i in range(100)]"])
    assert result.exit_code == 0
    events = EventRepository().list_by_job(job.job_id)
    assert len(events) <= 10
    output = "\n".join(event.message for event in events)
    assert "line-0" in output and "line-99" in output


def test_queue_passes_one_canonical_job_to_import(workspace, monkeypatch, tmp_path):
    from noir.application.import_service import ImportService
    from noir.application.jobs import TaskQueue

    cfg, ws = workspace
    upload = tmp_path / "queued.apk"
    upload.write_bytes(b"queued")
    seen = []

    def fake_import(_service, _path, **kwargs):
        seen.append(kwargs["job"].job_id)
        return {"project_id": ws.project_id}

    monkeypatch.setattr(ImportService, "import_apk", fake_import)
    queue = TaskQueue(cfg)
    job = queue.submit("import", ws.project_id, {"path": str(upload)})
    queue.execute(job)
    assert seen == [job.job_id]
    assert [item.job_id for item in JobRepository().list_by_project(ws.project_id)] == [job.job_id]


def test_build_uses_decoded_workspace_and_cleans_generated_files(workspace, monkeypatch):
    from noir.application.build_service import BuildService
    from noir.infrastructure.database.repositories import FileManifestRepository

    cfg, ws = workspace
    (ws.decoded_dir / "AndroidManifest.xml").write_text(
        '<manifest package="com.noir.performance"><application /></manifest>'
    )
    FileManifestRepository().save(ws.project_id, 1, ws.build_file_manifest())
    project = ProjectRepository().get(ws.project_id)
    project.workspace_revision = 1
    ProjectRepository().update(project)
    service = BuildService(cfg)

    def fake_build(decoded_dir, output_apk, **_kwargs):
        assert decoded_dir == ws.decoded_dir
        (decoded_dir / "build" / "apk").mkdir(parents=True)
        (decoded_dir / "build" / "apk" / "temporary").write_text("generated")
        with zipfile.ZipFile(output_apk, "w") as archive:
            archive.writestr("AndroidManifest.xml", "manifest")
        return SimpleNamespace(
            tool_version="test", duration_seconds=0.01, stdout="built", stderr=""
        )

    monkeypatch.setattr(service.apktool, "build", fake_build)
    result = service.build(ws.project_id)
    assert result.success
    assert not (ws.decoded_dir / "build").exists()
    assert not (ws.builds_dir / result.build_id / "workspace").exists()


def test_job_event_cursor_reads_only_new_events(workspace):
    from noir.domain.models import AuditEvent
    from noir.infrastructure.database.repositories import EventRepository

    _, ws = workspace
    job = JobInfo(project_id=ws.project_id, stage=WorkflowStage.PLANNING)
    JobRepository().create(job)
    repo = EventRepository()
    first = repo.create(AuditEvent(project_id=ws.project_id, job_id=job.job_id, message="first"))
    repo.create(AuditEvent(project_id=ws.project_id, job_id=job.job_id, message="second"))
    assert [event.message for event in repo.list_by_job(job.job_id, after_id=first.event_id)] == [
        "second"
    ]


def test_process_credentials_are_not_inherited_or_logged(monkeypatch):
    from noir.infrastructure.processes.runner import run_tool

    monkeypatch.setenv("GEMINI_API_KEY", "should-not-reach-child")
    result = run_tool([sys.executable, "-c", "import os; print(os.getenv('GEMINI_API_KEY'))"])
    assert result.stdout.strip() == "None"
    result = run_tool(
        [sys.executable, "-c", "import os; print(os.getenv('TEST_PASSWORD'))"],
        env={"TEST_PASSWORD": "test-secret-value"},
    )
    assert "test-secret-value" not in result.stdout
    assert "test-secret-value" not in " ".join(result.command)


def test_build_refuses_unrecorded_changes(workspace):
    from noir.application.build_service import BuildService, BuildServiceError

    cfg, ws = workspace
    (ws.decoded_dir / "sample.txt").write_text("not recorded")
    with pytest.raises(BuildServiceError, match="unrecorded"):
        BuildService(cfg).build(ws.project_id)


def test_signing_requires_explicit_confirmation(workspace):
    from noir.application.signing_service import SigningService, SigningServiceError

    cfg, ws = workspace
    with pytest.raises(SigningServiceError, match="confirmation"):
        SigningService(cfg).sign(ws.project_id, "build", "profile")


def test_api_queue_cancellation_sse_and_upload_limit(workspace):
    from fastapi.testclient import TestClient

    from noir.api.app import create_app
    from noir.domain.models import ApiToken
    from noir.infrastructure.database.repositories import TokenRepository

    cfg, ws = workspace
    cfg.max_upload_size = 10
    token = secrets.token_urlsafe(24)
    TokenRepository().create(ApiToken(token_hash=hashlib.sha256(token.encode()).hexdigest()))
    client = TestClient(create_app(cfg), headers={"Authorization": f"Bearer {token}"})
    job = client.app.state.queue.submit("build", ws.project_id, {"revision": 0})
    assert client.post(f"/v1/jobs/{job.job_id}/cancel").json()["state"] == "cancelled"
    events = client.get(f"/v1/jobs/{job.job_id}/events").text
    assert "event: done\ndata: " in events
    response = client.post("/v1/import?authorized=true", files={"file": ("large.apk", b"x" * 20)})
    assert response.status_code == 413
    assert not any(path.is_file() for path in (cfg.projects_dir.parent / "uploads").rglob("*"))


@pytest.mark.e2e
@pytest.mark.skipif(
    os.getenv("NOIR_RUN_E2E") != "1", reason="Set NOIR_RUN_E2E=1 for real Android tools"
)
def test_real_apk_toolchain(workspace):
    from noir.application.demo import run_offline_demo

    cfg, _ = workspace
    result = run_offline_demo(cfg)
    assert result["signed_apk"]
    assert any(item["type"] == "signed_apk" for item in result["export"]["files"])


@pytest.mark.e2e
@pytest.mark.skipif(
    os.getenv("NOIR_RUN_E2E") != "1", reason="Set NOIR_RUN_E2E=1 for real Android tools"
)
def test_real_api_import_and_queued_build(workspace):
    from fastapi.testclient import TestClient

    from noir.api.app import create_app
    from noir.application.demo import _build_fixture_apk
    from noir.domain.models import ApiToken
    from noir.infrastructure.database.repositories import TokenRepository

    cfg, _ = workspace
    fixture = _build_fixture_apk(cfg)
    token = secrets.token_urlsafe(24)
    TokenRepository().create(ApiToken(token_hash=hashlib.sha256(token.encode()).hexdigest()))

    def wait_for_job(client, job_id):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = client.get(f"/v1/jobs/{job_id}").json()
            if job["state"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                assert job["state"] == "succeeded", job
                return job
            time.sleep(0.1)
        pytest.fail("Real tool job did not finish")

    with TestClient(create_app(cfg), headers={"Authorization": f"Bearer {token}"}) as client:
        payload = fixture.read_bytes()
        response = client.post(
            "/v1/import?authorized=true",
            files={"file": (fixture.name, payload)},
            headers={"Idempotency-Key": "real-import"},
        )
        assert response.status_code == 202, response.text
        imported = wait_for_job(client, response.json()["job_id"])
        duplicate = client.post(
            "/v1/import?authorized=true",
            files={"file": (fixture.name, payload)},
            headers={"Idempotency-Key": "real-import"},
        )
        assert duplicate.json()["job_id"] == imported["job_id"]
        project = imported["project_id"]
        response = client.post(f"/v1/projects/{project}/build")
        assert response.status_code == 202, response.text
        built = wait_for_job(client, response.json()["job_id"])
        assert built["result_data"]["result"]["success"]
        stream = client.get(f"/v1/jobs/{built['job_id']}/events").text
        assert "Apktool" in stream and "event: done\n" in stream


def test_rejected_plan_never_rebuilds(workspace, monkeypatch, tmp_path):
    """Inject only the AI boundary to test a rejection without billable API calls."""
    from typer.testing import CliRunner

    import noir.application.ai_service as ai_service
    from noir.application.build_service import BuildService
    from noir.application.import_service import ImportService
    from noir.application.patch_service import PlanService
    from noir.cli.main import app
    from noir.domain.models import ChangePlan

    cfg, ws = workspace
    monkeypatch.setenv("GEMINI_API_KEY", "boundary-test-only")
    plan = PlanService(cfg).create_plan(
        ChangePlan(
            project_id=ws.project_id,
            workspace_revision=0,
            user_request="test rejection",
        )
    )
    monkeypatch.setattr(ImportService, "import_apk", lambda *a, **k: {"project_id": ws.project_id})
    monkeypatch.setattr(ai_service, "generate_plan", lambda *a, **k: plan)

    def must_not_build(*args, **kwargs):
        pytest.fail("Rejected plan reached rebuild")

    monkeypatch.setattr(BuildService, "build", must_not_build)
    request = tmp_path / "request.txt"
    request.write_text("test rejection")
    result = CliRunner().invoke(
        app,
        ["run", "owned.apk", "--authorized", "--request-file", str(request), "--allow-ai-upload"],
        input="n\n",
    )
    assert result.exit_code == 3, result.output
    assert "Workflow completed" not in result.output
    assert (ws.reports_dir / "audit_report.md").exists()


def test_existing_project_run_skips_import_and_keeps_approval_gate(
    workspace, monkeypatch, tmp_path
):
    from typer.testing import CliRunner

    import noir.application.ai_service as ai_service
    from noir.application.import_service import ImportService
    from noir.application.patch_service import PlanService
    from noir.cli.main import app
    from noir.domain.models import ChangePlan

    cfg, ws = workspace
    monkeypatch.setenv("GEMINI_API_KEY", "boundary-test-only")
    project = ProjectRepository().get(ws.project_id)
    project.authorization_acknowledged = True
    ProjectRepository().update(project)
    plan = PlanService(cfg).create_plan(
        ChangePlan(
            project_id=ws.project_id,
            workspace_revision=0,
            user_request="Rename app",
        )
    )
    monkeypatch.setattr(ImportService, "import_apk", lambda *a, **k: pytest.fail("Must not import"))
    monkeypatch.setattr(ai_service, "generate_plan", lambda *a, **k: plan)
    request = tmp_path / "rename.txt"
    request.write_text("Rename app")
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--project",
            ws.project_id,
            "--authorized",
            "--request-file",
            str(request),
            "--allow-ai-upload",
        ],
        input="n\n",
    )
    assert result.exit_code == 3, result.output
    assert "Using existing project:" in result.output
    assert "Approve this exact plan?" in result.output
    assert len(ProjectRepository().list_all()) == 1


def test_run_rejects_ambiguous_apk_and_project(workspace):
    from typer.testing import CliRunner

    from noir.cli.main import app

    _, ws = workspace
    result = CliRunner().invoke(
        app, ["run", "owned.apk", "--project", ws.project_id, "--authorized"]
    )
    assert result.exit_code == 2
    assert "not both" in result.output


def test_failed_ai_response_preserves_approval_and_can_retry(workspace, monkeypatch):
    from types import SimpleNamespace

    from google.genai import types
    from pydantic import SecretStr

    from noir.application.ai_service import generate_patch
    from noir.application.patch_service import PlanService
    from noir.domain.enums import ApprovalScope
    from noir.domain.models import ChangePlan, PlanFileChange
    from noir.infrastructure.ai.gemini import GeminiProvider, GeminiProviderError
    from noir.infrastructure.database.repositories import ApprovalRepository, PatchRepository

    cfg, ws = workspace
    cfg = cfg.model_copy(
        update={
            "ai_provider": "gemini",
            "ai_model": "gemini-3.7-flash",
            "gemini_api_key": SecretStr("boundary-test-only"),
        }
    )
    plan_service = PlanService(cfg)
    plan = plan_service.create_plan(
        ChangePlan(
            project_id=ws.project_id,
            workspace_revision=0,
            user_request="Change before to after",
            file_changes=[
                PlanFileChange(
                    relative_path="sample.txt",
                    operation=PatchOperationType.REPLACE_BLOCK,
                )
            ],
        )
    )
    plan_hash = plan.compute_hash()
    plan_service.approve_plan(ws.project_id, plan.plan_id, plan_hash)
    calls = []

    def invalid_response(**kwargs):
        calls.append(kwargs)
        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    finish_reason=types.FinishReason.STOP,
                    content=types.Content(
                        parts=[types.Part(text='{"operations":[{"new_content":"after')]
                    ),
                )
            ]
        )

    client = SimpleNamespace(models=SimpleNamespace(generate_content=invalid_response))
    monkeypatch.setattr(GeminiProvider, "_get_client", lambda self: client)
    with pytest.raises(GeminiProviderError, match="No partial response was used"):
        generate_patch(cfg, ws.project_id, plan.plan_id)
    assert len(calls) == 2
    assert PatchRepository().list_by_project(ws.project_id) == []
    assert (ws.decoded_dir / "sample.txt").read_text() == "before\n"
    assert ProjectRepository().get(ws.project_id).workspace_revision == 0
    assert ApprovalRepository().find_valid(ws.project_id, ApprovalScope.PLAN, plan_hash, 0)
    assert [item.scope for item in ApprovalRepository().list_by_project(ws.project_id)] == [
        ApprovalScope.PLAN
    ]

    response_text = json.dumps(
        {
            "operations": [
                {
                    "relative_path": "sample.txt",
                    "operation": "replace_block",
                    "match_content": "before",
                    "new_content": "after",
                }
            ]
        }
    )
    client.models.generate_content = lambda **kwargs: types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                finish_reason=types.FinishReason.STOP,
                content=types.Content(parts=[types.Part(text=response_text)]),
            ),
        ]
    )
    patch = generate_patch(cfg, ws.project_id, plan.plan_id)
    assert len(PatchRepository().list_by_project(ws.project_id)) == 1
    assert not PatchRepository().is_applied(patch.patch_id)
    assert (ws.decoded_dir / "sample.txt").read_text() == "before\n"
    assert not ApprovalRepository().find_valid(
        ws.project_id, ApprovalScope.PATCH, patch.compute_hash(), 0
    )
