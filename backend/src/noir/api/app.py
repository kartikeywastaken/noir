"""NOIR HTTP API — FastAPI application with authentication and all routes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from noir.domain.config import NoirConfig, get_config
from noir.infrastructure.database.engine import init_db
from noir.infrastructure.database.repositories import TokenRepository

# ── Auth ─────────────────────────────────────────────────────────────


def _verify_token(authorization: str | None = Header(None, alias="Authorization")) -> str:
    """Verify Bearer token against stored hashes."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization[7:]
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    repo = TokenRepository()
    found = repo.find_by_hash(token_hash)
    if not found:
        raise HTTPException(status_code=401, detail="Invalid API token")
    return token


# ── Request/Response Models ──────────────────────────────────────────


class ImportRequest(BaseModel):
    authorized: bool = False


class PlanCreateRequest(BaseModel):
    user_request: str
    allow_ai_upload: bool = False


class PlanApproveRequest(BaseModel):
    hash: str


class PatchApproveRequest(BaseModel):
    hash: str


class ManualRecordRequest(BaseModel):
    message: str = ""


class SignRequest(BaseModel):
    build_id: str
    profile: str
    confirm: bool = False


class FileReplaceRequest(BaseModel):
    relative_path: str
    content: str
    expected_revision: int


class ErrorResponse(BaseModel):
    error: str
    code: str = "error"


# ── App Factory ──────────────────────────────────────────────────────


def create_app(config: NoirConfig | None = None) -> FastAPI:
    """Create the FastAPI application."""
    cfg = config or get_config()
    cfg.ensure_directories()
    init_db(cfg.effective_database_url)

    from noir.application.jobs import TaskQueue

    queue = TaskQueue(cfg)

    @asynccontextmanager
    async def lifespan(app):
        queue.start()
        try:
            yield
        finally:
            queue.close()

    app = FastAPI(
        lifespan=lifespan,
        title="NOIR API",
        version="0.1.0",
        description="Local-first APK analysis and modification API",
        docs_url="/docs",
        openapi_url="/v1/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],  # No CORS by default
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Store config in app state
    app.state.config = cfg
    app.state.queue = queue

    def get_cfg() -> NoirConfig:
        return cfg

    # ── Health ────────────────────────────────────────────────────

    @app.get("/v1/health")
    def health():
        from noir.application.doctor import run_doctor

        report = run_doctor(cfg)
        return {
            "status": "ok",
            "version": "0.1.0",
            "capabilities": {
                "import": report.all_required_available,
                "build": report.build_capable,
                "ai": report.ai_configured,
                "device": report.device_capable,
            },
        }

    # ── Projects ──────────────────────────────────────────────────

    @app.get("/v1/projects", dependencies=[Depends(_verify_token)])
    def list_projects(offset: int = 0, limit: int = 50):
        from noir.infrastructure.database.repositories import ProjectRepository

        projects = ProjectRepository().list_all()
        data = [p.model_dump(mode="json") for p in projects[offset : offset + limit]]
        return {"projects": data, "total": len(projects)}

    @app.get("/v1/projects/{project_id}", dependencies=[Depends(_verify_token)])
    def get_project(project_id: str):
        from noir.infrastructure.database.repositories import ProjectRepository

        project = ProjectRepository().get(project_id)
        if not project:
            raise HTTPException(404, "Project not found")
        return project.model_dump(mode="json")

    # ── Import ────────────────────────────────────────────────────

    @app.post("/v1/import", status_code=202, dependencies=[Depends(_verify_token)])
    async def import_apk(
        file: Annotated[UploadFile, File()],
        authorized: bool = Query(False),
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        if not authorized:
            raise HTTPException(400, "Authorization required")
        from uuid import uuid4

        from noir.infrastructure.database.repositories import JobRepository
        from noir.infrastructure.filesystem.workspace import compute_file_hash

        uploads = Path(cfg.data_dir) / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(suffix=".apk", prefix="upload-", dir=uploads)
        tmp = Path(name)
        try:
            size = 0
            with os.fdopen(fd, "wb") as handle:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > cfg.max_upload_size:
                        raise HTTPException(413, "APK exceeds upload limit")
                    handle.write(chunk)
            digest = compute_file_hash(tmp)
            if idempotency_key:
                for existing in JobRepository().list_all():
                    if existing.result_data.get("idempotency_key") == idempotency_key:
                        if existing.result_data.get("payload", {}).get("sha256") != digest:
                            raise HTTPException(409, "Idempotency key has different content")
                        tmp.unlink()
                        return existing.model_dump(mode="json")
            job = queue.submit(
                "import", uuid4().hex[:16], {"path": str(tmp), "sha256": digest}, idempotency_key
            )
            return job.model_dump(mode="json")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    # ── Analysis ──────────────────────────────────────────────────

    @app.get("/v1/projects/{project_id}/analysis", dependencies=[Depends(_verify_token)])
    def get_analysis(project_id: str):
        from noir.analysis.analyzer import AnalysisService

        result = AnalysisService(cfg).get_analysis(project_id)
        if not result:
            raise HTTPException(404, "Analysis not found")
        return result.model_dump(mode="json")

    # ── Files ─────────────────────────────────────────────────────

    @app.get("/v1/projects/{project_id}/files", dependencies=[Depends(_verify_token)])
    def list_files(project_id: str, subdir: str = ""):
        from noir.application.file_service import FileService

        files = FileService(cfg).list_files(project_id, subdir)
        return {"files": files}

    @app.get("/v1/projects/{project_id}/files/read", dependencies=[Depends(_verify_token)])
    def read_file(project_id: str, path: str = Query(...)):
        from noir.application.file_service import FileService

        try:
            content = FileService(cfg).read_file(project_id, path)
            return {"path": path, "content": content}
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(404, str(e)) from None

    @app.get("/v1/projects/{project_id}/files/search", dependencies=[Depends(_verify_token)])
    def search_files(project_id: str, q: str = Query(...)):
        from noir.application.file_service import FileService

        results = FileService(cfg).search(project_id, q)
        return {"results": results}

    @app.put("/v1/projects/{project_id}/files", dependencies=[Depends(_verify_token)])
    def replace_file(project_id: str, req: FileReplaceRequest):
        from noir.application.manual_service import ManualService
        from noir.infrastructure.database.repositories import ProjectRepository
        from noir.infrastructure.filesystem.workspace import ProjectWorkspace
        from noir.security.locking import project_lock

        with project_lock(cfg, project_id):
            project = ProjectRepository().get(project_id)
            if not project or project.workspace_revision != req.expected_revision:
                raise HTTPException(409, "Project missing or stale revision")
            if not ManualService(cfg).get_active_session(project_id):
                raise HTTPException(400, "No active manual session")
            if len(req.content.encode()) > 1_000_000:
                raise HTTPException(413, "Text edit exceeds size limit")
            target = ProjectWorkspace(project_id, cfg).safe_path(req.relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(req.content, encoding="utf-8")
        return {"replaced": req.relative_path}

    # ── Plans ─────────────────────────────────────────────────────

    @app.post("/v1/projects/{project_id}/plans", dependencies=[Depends(_verify_token)])
    def create_plan(project_id: str, req: PlanCreateRequest):
        if not req.allow_ai_upload:
            raise HTTPException(400, "Must set allow_ai_upload=true")

        from noir.application.ai_service import generate_plan

        plan = generate_plan(cfg, project_id, req.user_request, req.allow_ai_upload)

        data = plan.model_dump(mode="json")
        data["plan_hash"] = plan.compute_hash()
        return data

    @app.get("/v1/projects/{project_id}/plans", dependencies=[Depends(_verify_token)])
    def list_plans(project_id: str):
        from noir.application.patch_service import PlanService

        plans = PlanService(cfg).list_plans(project_id)
        return {
            "plans": [
                {"plan_id": p.plan_id, "hash": p.compute_hash(), "request": p.user_request}
                for p in plans
            ]
        }

    @app.get(
        "/v1/projects/{project_id}/plans/{plan_id}", dependencies=[Depends(_verify_token)]
    )
    def get_plan(project_id: str, plan_id: str):
        from noir.application.patch_service import PlanService

        plan = PlanService(cfg).get_plan(plan_id)
        if not plan or plan.project_id != project_id:
            raise HTTPException(404, "Plan not found")
        data = plan.model_dump(mode="json")
        data["plan_hash"] = plan.compute_hash()
        return data

    @app.post(
        "/v1/projects/{project_id}/plans/{plan_id}/approve", dependencies=[Depends(_verify_token)]
    )
    def approve_plan(project_id: str, plan_id: str, req: PlanApproveRequest):
        from noir.application.patch_service import PlanService, PlanServiceError

        try:
            approval = PlanService(cfg).approve_plan(project_id, plan_id, req.hash)
            return approval.model_dump(mode="json")
        except PlanServiceError as e:
            raise HTTPException(400, str(e)) from e

    @app.post(
        "/v1/projects/{project_id}/plans/{plan_id}/reject", dependencies=[Depends(_verify_token)]
    )
    def reject_plan(project_id: str, plan_id: str):
        from noir.application.patch_service import PlanService, PlanServiceError

        try:
            PlanService(cfg).reject_plan(project_id, plan_id)
            return {"rejected": plan_id}
        except PlanServiceError as e:
            raise HTTPException(400, str(e)) from e

    # ── Patches ───────────────────────────────────────────────────

    @app.post("/v1/projects/{project_id}/patches", dependencies=[Depends(_verify_token)])
    def generate_patch(project_id: str, plan_id: str = Query(...)):
        from noir.application.ai_service import generate_patch as create_patch

        patch = create_patch(cfg, project_id, plan_id)

        data = patch.model_dump(mode="json")
        data["patch_hash"] = patch.compute_hash()
        return data

    @app.post(
        "/v1/projects/{project_id}/patches/{patch_id}/approve",
        dependencies=[Depends(_verify_token)],
    )
    def approve_patch(project_id: str, patch_id: str, req: PatchApproveRequest):
        from noir.application.patch_service import PatchService, PlanServiceError

        try:
            approval = PatchService(cfg).approve_patch(project_id, patch_id, req.hash)
            return approval.model_dump(mode="json")
        except PlanServiceError as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/v1/projects/{project_id}/patches", dependencies=[Depends(_verify_token)])
    def list_patches(project_id: str):
        from noir.application.patch_service import PatchService

        patches = PatchService(cfg).list_patches(project_id)
        return {
            "patches": [
                {
                    "patch_id": p.patch_id,
                    "plan_id": p.plan_id,
                    "hash": p.compute_hash(),
                    "workspace_revision": p.workspace_revision,
                    "operation_count": len(p.operations),
                }
                for p in patches
            ]
        }

    @app.get(
        "/v1/projects/{project_id}/patches/{patch_id}", dependencies=[Depends(_verify_token)]
    )
    def get_patch(project_id: str, patch_id: str):
        from noir.application.patch_service import PatchService

        patch = PatchService(cfg).get_patch(patch_id)
        if not patch or patch.project_id != project_id:
            raise HTTPException(404, "Patch not found")
        data = patch.model_dump(mode="json")
        data["patch_hash"] = patch.compute_hash()
        return data

    @app.get(
        "/v1/projects/{project_id}/patches/{patch_id}/diff", dependencies=[Depends(_verify_token)]
    )
    def get_patch_diff(project_id: str, patch_id: str):
        from noir.application.patch_service import PatchService

        diff = PatchService(cfg).show_diff(project_id, patch_id)
        return {"diff": diff}

    @app.post(
        "/v1/projects/{project_id}/patches/{patch_id}/apply", dependencies=[Depends(_verify_token)]
    )
    def apply_patch(project_id: str, patch_id: str):
        from noir.application.patch_service import PatchService, PlanServiceError

        try:
            result = PatchService(cfg).apply_patch(project_id, patch_id)
            return result
        except PlanServiceError as e:
            raise HTTPException(400, str(e)) from None

    @app.post(
        "/v1/projects/{project_id}/patches/{patch_id}/undo", dependencies=[Depends(_verify_token)]
    )
    def undo_patch(project_id: str, patch_id: str):
        from noir.application.patch_service import PatchService, PlanServiceError

        try:
            result = PatchService(cfg).undo_patch(project_id, patch_id)
            return result
        except PlanServiceError as e:
            raise HTTPException(400, str(e)) from None

    # ── Manual ────────────────────────────────────────────────────

    @app.post("/v1/projects/{project_id}/manual/begin", dependencies=[Depends(_verify_token)])
    def manual_begin(project_id: str):
        from noir.application.manual_service import ManualEditError, ManualService

        try:
            session = ManualService(cfg).begin_session(project_id)
            return session.model_dump(mode="json")
        except ManualEditError as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/v1/projects/{project_id}/manual/session", dependencies=[Depends(_verify_token)])
    def get_manual_session(project_id: str):
        from noir.application.manual_service import ManualService

        session = ManualService(cfg).get_active_session(project_id)
        if not session:
            return {"active": False}
        return {"active": True, "session": session.model_dump(mode="json")}

    @app.post("/v1/projects/{project_id}/manual/record", dependencies=[Depends(_verify_token)])
    def manual_record(project_id: str, req: ManualRecordRequest):
        from noir.application.manual_service import ManualEditError, ManualService

        try:
            result = ManualService(cfg).record_changes(project_id, req.message)
            return result
        except ManualEditError as e:
            raise HTTPException(400, str(e)) from None

    # ── Validation ────────────────────────────────────────────────

    @app.post("/v1/projects/{project_id}/validate", dependencies=[Depends(_verify_token)])
    def validate_project(project_id: str):
        from noir.validation.workspace_validator import ValidationService

        result = ValidationService(cfg).validate(project_id)
        return result.model_dump(mode="json")

    # ── Build ─────────────────────────────────────────────────────

    @app.post(
        "/v1/projects/{project_id}/build", status_code=202, dependencies=[Depends(_verify_token)]
    )
    def build_project(
        project_id: str, idempotency_key: str | None = Header(None, alias="Idempotency-Key")
    ):
        from noir.infrastructure.database.repositories import ProjectRepository

        project = ProjectRepository().get(project_id)
        if not project:
            raise HTTPException(404, "Project not found")
        job = queue.submit(
            "build", project_id, {"revision": project.workspace_revision}, idempotency_key
        )
        return job.model_dump(mode="json")

    @app.get("/v1/projects/{project_id}/builds", dependencies=[Depends(_verify_token)])
    def list_builds(project_id: str):
        from noir.application.build_service import BuildService

        builds = BuildService(cfg).list_builds(project_id)
        return {"builds": [b.model_dump(mode="json") for b in builds]}

    # ── Sign ──────────────────────────────────────────────────────

    @app.post("/v1/projects/{project_id}/sign", dependencies=[Depends(_verify_token)])
    def sign_project(project_id: str, req: SignRequest):
        if not req.confirm:
            raise HTTPException(400, "Signing requires confirm=true")
        from noir.application.signing_service import SigningService, SigningServiceError

        try:
            result = SigningService(cfg).sign(
                project_id, req.build_id, req.profile, confirmed=req.confirm
            )
            return result.model_dump(mode="json")
        except SigningServiceError as e:
            raise HTTPException(400, str(e)) from None

    # ── Verify ────────────────────────────────────────────────────

    @app.get(
        "/v1/projects/{project_id}/builds/{build_id}/verify", dependencies=[Depends(_verify_token)]
    )
    def verify_build(project_id: str, build_id: str):
        from noir.infrastructure.android_tools.tools import verify_signature
        from noir.infrastructure.database.repositories import BuildRepository

        build = BuildRepository().get(build_id)
        if not build or build.project_id != project_id or not build.signed_apk_path:
            raise HTTPException(404, "No signed APK")
        result = verify_signature(cfg, Path(build.signed_apk_path))
        return result

    # ── Audit ─────────────────────────────────────────────────────

    @app.get("/v1/projects/{project_id}/audit", dependencies=[Depends(_verify_token)])
    def get_audit(project_id: str, format: str = "json"):
        from noir.auditing.reporter import AuditReporter

        reporter = AuditReporter(cfg)
        if format == "markdown":
            return JSONResponse({"markdown": reporter.generate_markdown(project_id)})
        return json.loads(reporter.generate_json(project_id))

    # ── Export / Download ─────────────────────────────────────────

    @app.get(
        "/v1/projects/{project_id}/builds/{build_id}/download",
        dependencies=[Depends(_verify_token)],
    )
    def download_artifact(project_id: str, build_id: str, artifact: str = "signed"):
        from noir.infrastructure.database.repositories import BuildRepository

        build = BuildRepository().get(build_id)
        if not build or build.project_id != project_id:
            raise HTTPException(404, "Build not found")

        path_map = {
            "unsigned": build.unsigned_apk_path,
            "aligned": build.aligned_apk_path,
            "signed": build.signed_apk_path,
        }
        apk_path = path_map.get(artifact)
        if not apk_path or not Path(apk_path).exists():
            raise HTTPException(404, f"Artifact '{artifact}' not found")

        return FileResponse(apk_path, filename=Path(apk_path).name)

    # ── Jobs ──────────────────────────────────────────────────────

    @app.get("/v1/jobs", dependencies=[Depends(_verify_token)])
    def list_jobs(project_id: str = Query(None)):
        from noir.infrastructure.database.repositories import JobRepository

        repo = JobRepository()
        jobs = repo.list_by_project(project_id) if project_id else repo.list_all()
        return {"jobs": [j.model_dump(mode="json") for j in jobs]}

    @app.get("/v1/jobs/{job_id}", dependencies=[Depends(_verify_token)])
    def get_job(job_id: str):
        from noir.infrastructure.database.repositories import JobRepository

        job = JobRepository().get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return job.model_dump(mode="json")

    @app.get("/v1/projects/{project_id}/events", dependencies=[Depends(_verify_token)])
    def list_events(project_id: str, after: str = None, limit: int = 100):
        from noir.infrastructure.database.repositories import EventRepository

        events = EventRepository().list_by_project(project_id, after_id=after, limit=limit)
        return {"events": [e.model_dump(mode="json") for e in events]}

    # ── Keys ──────────────────────────────────────────────────────

    @app.get("/v1/keys", dependencies=[Depends(_verify_token)])
    def list_signing_profiles():
        from noir.application.signing_service import SigningService

        profiles = SigningService(cfg).list_profiles()
        return {"profiles": [{"name": p.name, "type": p.profile_type.value} for p in profiles]}

    @app.post("/v1/jobs/{job_id}/cancel", dependencies=[Depends(_verify_token)])
    def cancel_job(job_id: str):
        from noir.infrastructure.database.repositories import JobRepository

        return JobRepository().request_cancel(job_id).model_dump(mode="json")

    @app.get("/v1/jobs/{job_id}/events", dependencies=[Depends(_verify_token)])
    async def stream_job_events(job_id: str, after: str | None = None):
        from noir.application.jobs import TERMINAL
        from noir.infrastructure.database.repositories import EventRepository, JobRepository

        if not JobRepository().get(job_id):
            raise HTTPException(404, "Job not found")

        async def events():
            seen = set()
            if after is not None:
                historical = EventRepository().list_by_job(job_id)
                ids = [event.event_id for event in historical]
                if after not in ids:
                    yield 'event: error\ndata: {"error":"Unknown event cursor"}\n\n'
                    return
                seen.update(ids[: ids.index(after) + 1])
            while True:
                for event in EventRepository().list_by_job(job_id):
                    if event.event_id not in seen:
                        seen.add(event.event_id)
                        yield f"id: {event.event_id}\ndata: {event.model_dump_json()}\n\n"
                job = JobRepository().get(job_id)
                if not job or job.state in TERMINAL:
                    data = job.model_dump_json() if job else "{}"
                    yield f"event: done\ndata: {data}\n\n"
                    return
                await asyncio.sleep(0.2)

        return StreamingResponse(events(), media_type="text/event-stream")

    from noir.application.build_service import BuildServiceError
    from noir.application.manual_service import ManualEditError
    from noir.application.patch_service import PlanServiceError
    from noir.application.signing_service import SigningServiceError
    from noir.infrastructure.ai.gemini import GeminiProviderError
    from noir.infrastructure.filesystem.workspace import WorkspaceError
    from noir.patches.engine import PatchError

    async def application_error(request, exc):
        return JSONResponse(status_code=400, content={"error": str(exc)})

    for exception in (
        ValueError,
        PlanServiceError,
        WorkspaceError,
        PatchError,
        GeminiProviderError,
        BuildServiceError,
        ManualEditError,
        SigningServiceError,
    ):
        app.add_exception_handler(exception, application_error)

    return app
