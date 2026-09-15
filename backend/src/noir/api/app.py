"""NOIR HTTP API — FastAPI application with authentication and all routes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from noir.application.access_service import AccessError, AccessService
from noir.domain.config import NoirConfig, get_config
from noir.domain.models import ApiToken
from noir.infrastructure.database.engine import init_db
from noir.infrastructure.database.repositories import TokenRepository

# ── Auth ─────────────────────────────────────────────────────────────

_token_cache: dict[str, tuple[ApiToken, float]] = {}
_token_cache_lock = threading.Lock()
_TOKEN_CACHE_TTL = 5.0  # seconds — revocation takes effect within this window
_TOKEN_CACHE_MAX = 128


def _verify_token(
    request: Request, authorization: str | None = Header(None, alias="Authorization")
) -> ApiToken:
    """Authenticate and guard every resource route, including job streams/cancel.

    All private routes depend on this guard. Ownership is derived exclusively
    from the server-side token record, never a client-supplied user identifier.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization[7:]
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = time.monotonic()
    found: ApiToken | None = None
    with _token_cache_lock:
        cached = _token_cache.get(token_hash)
        if cached and now - cached[1] < _TOKEN_CACHE_TTL:
            found = cached[0]
    if found is None:
        repo = TokenRepository()
        found = repo.find_by_hash(token_hash)
        if found:
            with _token_cache_lock:
                _token_cache[token_hash] = (found, now)
                # Evict oldest entries if cache exceeds max size
                if len(_token_cache) > _TOKEN_CACHE_MAX:
                    oldest_key = min(_token_cache, key=lambda k: _token_cache[k][1])
                    del _token_cache[oldest_key]
    if not found:
        raise HTTPException(status_code=401, detail="Invalid API token")
    access = AccessService()
    project_id = request.path_params.get("project_id")
    if project_id and not access.owns_project(found.user_id, project_id):
        raise HTTPException(404, "Project not found")
    job_id = request.path_params.get("job_id")
    if job_id:
        from noir.infrastructure.database.repositories import JobRepository

        job = JobRepository().get(job_id)
        if not job or not access.owns_project(found.user_id, job.project_id):
            raise HTTPException(404, "Job not found")
    return found


Principal = Annotated[ApiToken, Depends(_verify_token)]


# ── Request/Response Models ──────────────────────────────────────────


class ImportRequest(BaseModel):
    authorized: bool = False


class UploadCreateRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    upload_mode: Literal["auto", "s3", "proxy"] = "auto"


class S3UploadPartAuthorization(BaseModel):
    part_number: int = Field(ge=1, le=10_000)
    checksum_sha256: str = Field(min_length=44, max_length=44)


class S3UploadPartCompletion(S3UploadPartAuthorization):
    etag: str = Field(min_length=1, max_length=128)
    size: int = Field(gt=0)


class S3UploadPartAuthorizationRequest(BaseModel):
    parts: list[S3UploadPartAuthorization] = Field(min_length=1, max_length=100)


class S3UploadPartCompletionRequest(BaseModel):
    parts: list[S3UploadPartCompletion] = Field(default_factory=list, max_length=100)


class InviteRedeemRequest(BaseModel):
    code: str = Field(min_length=1, max_length=4096)


class WorkflowPrepareRequest(BaseModel):
    user_request: str = Field(min_length=1, max_length=16000)
    allow_ai_upload: bool = False
    revision: int = Field(ge=0)
    model: Literal[
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-flash-latest",
        "gemini-3.7-flash",
        "gemini-3.8-flash",
        "gemini-3.1-pro-preview",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash",
        "openrouter:nvidia/nemotron-3.5-lightning:free",
    ] = "gemini-3.6-flash"



class WorkflowFinishRequest(BaseModel):
    plan_id: str
    patch_id: str
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    patch_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision: int = Field(ge=0)
    confirm: bool = False


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
        description="Invite-only APK analysis and modification API with private workspaces",
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

    from noir.application.upload_service import ResumableUploadService, S3MultipartUploadService

    uploads = ResumableUploadService(cfg, queue)
    s3_uploads = S3MultipartUploadService(cfg, queue)
    app.state.uploads = uploads
    app.state.s3_uploads = s3_uploads

    def get_cfg() -> NoirConfig:
        return cfg

    def review_state(project_id: str, target_hash: str, revision: int, scope: str) -> dict:
        """Read persisted review state; mutation services remain authoritative."""
        from noir.domain.enums import ApprovalScope
        from noir.infrastructure.database.repositories import ApprovalRepository, ProjectRepository

        project = ProjectRepository().get(project_id)
        if not project:
            raise HTTPException(404, "Project not found")
        stale = revision != project.workspace_revision
        approved = (
            not stale
            and ApprovalRepository().find_valid(
                project_id, ApprovalScope(scope), target_hash, project.workspace_revision
            )
            is not None
        )
        return {
            "approved": approved,
            "stale": stale,
            "current_revision": project.workspace_revision,
        }

    # ── Health ────────────────────────────────────────────────────

    health_cache = {}
    health_lock = threading.Lock()

    @app.get("/v1/health")
    def health():
        from noir.application.doctor import run_doctor

        # Doctor launches tool/version probes. Do not spawn several JVMs on every
        # app resume/health poll; refresh capabilities at most once per minute.
        with health_lock:
            if time.monotonic() >= health_cache.get("expires", 0):
                health_cache.update(report=run_doctor(cfg), expires=time.monotonic() + 60)
            report = health_cache["report"]
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

    @app.post("/v1/auth/redeem")
    def redeem_invite(req: InviteRedeemRequest):
        try:
            result = AccessService().redeem(req.code)
        except AccessError as exc:
            raise HTTPException(400, str(exc)) from None
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/v1/auth/me")
    def current_user(principal: Principal):
        return AccessService().user(principal.user_id)

    @app.post("/v1/auth/logout")
    def logout(principal: Principal):
        AccessService().logout(principal.token_id)
        # Evict from token cache so revocation takes effect immediately
        with _token_cache_lock:
            _token_cache.pop(principal.token_hash, None)
        return {"signed_out": True}

    @app.get("/v1/history")
    def build_history(
        principal: Principal,
        offset: int = Query(0, ge=0),
        limit: int = Query(30, ge=1, le=100),
    ):
        return AccessService().history(principal.user_id, offset=offset, limit=limit)

    @app.get("/v1/projects", dependencies=[Depends(_verify_token)])
    def list_projects(
        principal: Principal,
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=100),
    ):
        from noir.infrastructure.database.repositories import ProjectRepository

        projects = ProjectRepository().list_all(user_id=principal.user_id)
        data = [p.model_dump(mode="json") for p in projects[offset : offset + limit]]
        return {"projects": data, "total": len(projects)}

    @app.get("/v1/projects/{project_id}", dependencies=[Depends(_verify_token)])
    def get_project(project_id: str):
        from noir.infrastructure.database.repositories import ProjectRepository

        project = ProjectRepository().get(project_id)
        if not project:
            raise HTTPException(404, "Project not found")
        return project.model_dump(mode="json")

    # ── Resumable upload / import ─────────────────────────────────

    def upload_failure(exc):
        from noir.application.upload_service import UploadError

        if isinstance(exc, UploadError):
            raise HTTPException(exc.status_code, str(exc)) from exc
        raise exc

    @app.post("/v1/uploads", status_code=201, dependencies=[Depends(_verify_token)])
    def begin_upload(
        req: UploadCreateRequest,
        principal: Principal,
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        try:
            if req.upload_mode != "proxy" and cfg.artifact_store == "s3":
                if req.sha256:
                    return s3_uploads.begin(
                        user_id=principal.user_id,
                        idempotency_key=idempotency_key or "",
                        filename=req.filename,
                        size=req.size,
                        sha256=req.sha256,
                    ).public()
                if req.upload_mode == "s3":
                    raise HTTPException(400, "Direct S3 upload requires the APK SHA-256")
            elif req.upload_mode == "s3":
                raise HTTPException(503, "Direct S3 upload is unavailable")
            return uploads.begin(
                user_id=principal.user_id,
                idempotency_key=idempotency_key or "",
                filename=req.filename,
                size=req.size,
            ).public()
        except Exception as exc:
            upload_failure(exc)

    @app.get("/v1/uploads/{upload_id}", dependencies=[Depends(_verify_token)])
    def upload_status(upload_id: str, principal: Principal):
        from noir.application.upload_service import UploadError

        try:
            try:
                return s3_uploads.status(
                    upload_id=upload_id, user_id=principal.user_id
                ).public()
            except UploadError as exc:
                if exc.status_code != 404:
                    raise
            return uploads.status(upload_id=upload_id, user_id=principal.user_id).public()
        except Exception as exc:
            upload_failure(exc)

    @app.post(
        "/v1/uploads/{upload_id}/parts/presign",
        dependencies=[Depends(_verify_token)],
    )
    def presign_upload_parts(
        upload_id: str,
        req: S3UploadPartAuthorizationRequest,
        principal: Principal,
    ):
        try:
            parts = s3_uploads.presign_parts(
                upload_id=upload_id,
                user_id=principal.user_id,
                parts=[part.model_dump() for part in req.parts],
            )
            return {"upload_id": upload_id, "parts": parts}
        except Exception as exc:
            upload_failure(exc)

    @app.put(
        "/v1/uploads/{upload_id}/parts",
        dependencies=[Depends(_verify_token)],
    )
    def report_upload_parts(
        upload_id: str,
        req: S3UploadPartCompletionRequest,
        principal: Principal,
    ):
        if not req.parts:
            raise HTTPException(400, "At least one completed part is required")
        try:
            return s3_uploads.report_parts(
                upload_id=upload_id,
                user_id=principal.user_id,
                parts=[part.model_dump() for part in req.parts],
            ).public()
        except Exception as exc:
            upload_failure(exc)

    @app.patch("/v1/uploads/{upload_id}", dependencies=[Depends(_verify_token)])
    async def append_upload(
        upload_id: str,
        request: Request,
        principal: Principal,
        upload_offset: int = Header(..., alias="Upload-Offset", ge=0),
    ):
        from noir.application.upload_service import UploadError

        try:
            s3_uploads.status(upload_id=upload_id, user_id=principal.user_id)
        except UploadError as exc:
            if exc.status_code != 404:
                upload_failure(exc)
        else:
            raise HTTPException(409, "This upload sends parts directly to S3")
        content_length = request.headers.get("content-length")
        if not content_length:
            raise HTTPException(411, "Content-Length is required for upload chunks")
        try:
            if int(content_length) > cfg.max_upload_chunk_size:
                raise HTTPException(413, "Upload chunk exceeds server limit")
        except ValueError as exc:
            raise HTTPException(400, "Invalid Content-Length") from exc
        chunk = await request.body()
        try:
            # append performs positional disk I/O and waits for a durable group
            # commit. Running it on the event loop serialized otherwise parallel
            # range requests behind the first fsync waiter.
            session = await run_in_threadpool(
                uploads.append,
                upload_id=upload_id,
                user_id=principal.user_id,
                offset=upload_offset,
                chunk=chunk,
            )
            return JSONResponse(session.public(), headers={"Upload-Offset": str(session.offset)})
        except Exception as exc:
            upload_failure(exc)

    @app.post(
        "/v1/uploads/{upload_id}/complete",
        status_code=202,
        dependencies=[Depends(_verify_token)],
    )
    def complete_upload(
        upload_id: str,
        principal: Principal,
        req: S3UploadPartCompletionRequest | None = None,
        authorized: bool = Query(False),
    ):
        from noir.application.upload_service import UploadError

        if not authorized:
            raise HTTPException(400, "Authorization required")
        try:
            try:
                s3_uploads.status(upload_id=upload_id, user_id=principal.user_id)
            except UploadError as exc:
                if exc.status_code != 404:
                    raise
            else:
                if req and req.parts:
                    s3_uploads.report_parts(
                        upload_id=upload_id,
                        user_id=principal.user_id,
                        parts=[part.model_dump() for part in req.parts],
                    )
                return s3_uploads.complete(
                    upload_id=upload_id, user_id=principal.user_id
                ).model_dump(mode="json")
            return uploads.complete(upload_id=upload_id, user_id=principal.user_id).model_dump(
                mode="json"
            )
        except Exception as exc:
            upload_failure(exc)

    # ── Legacy one-request import ─────────────────────────────────

    @app.post("/v1/import", status_code=202, dependencies=[Depends(_verify_token)])
    async def import_apk(
        file: Annotated[UploadFile, File()],
        principal: Annotated[ApiToken, Depends(_verify_token)],
        authorized: bool = Query(False),
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        if not authorized:
            raise HTTPException(400, "Authorization required")
        if idempotency_key:
            idempotency_key = f"{principal.user_id}:{idempotency_key}"
        from uuid import uuid4

        from noir.infrastructure.database.repositories import JobRepository

        uploads = Path(cfg.data_dir) / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(suffix=".apk", prefix="upload-", dir=uploads)
        tmp = Path(name)
        try:
            size = 0
            hasher = hashlib.sha256()
            with os.fdopen(fd, "wb") as handle:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > cfg.max_upload_size:
                        raise HTTPException(413, "APK exceeds upload limit")
                    handle.write(chunk)
                    hasher.update(chunk)
            digest = hasher.hexdigest()
            if idempotency_key:
                existing = JobRepository().find_by_idempotency(
                    idempotency_key, user_id=principal.user_id
                )
                if existing:
                    if existing.result_data.get("payload", {}).get("sha256") != digest:
                        raise HTTPException(409, "Idempotency key has different content")
                    tmp.unlink()
                    return existing.model_dump(mode="json")
            project_id = uuid4().hex[:16]
            AccessService().claim_project(principal.user_id, project_id)
            job = queue.submit(
                "import",
                project_id,
                {
                    "path": str(tmp),
                    "sha256": digest,
                    "size": size,
                    "move_input": True,
                    "original_filename": Path(
                        (file.filename or "uploaded.apk").replace("\\", "/")
                    ).name,
                },
                idempotency_key,
            )
            return job.model_dump(mode="json")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    # ── Analysis ──────────────────────────────────────────────────

    @app.post("/v1/projects/{project_id}/workflow/prepare", status_code=202)
    def prepare_workflow(
        project_id: str,
        req: WorkflowPrepareRequest,
        principal: Principal,
        idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
    ):
        if not req.allow_ai_upload:
            raise HTTPException(400, "Consent is required to send APK context to Gemini")
        try:
            job = queue.submit(
                "workflow_prepare",
                project_id,
                req.model_dump(),
                f"{principal.user_id}:prepare:{idempotency_key}",
            )
            return job.model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/v1/projects/{project_id}/workflow/finish", status_code=202)
    def finish_workflow(
        project_id: str,
        req: WorkflowFinishRequest,
        principal: Principal,
        idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
    ):
        from noir.application.workflow_service import check_finish

        payload = {**req.model_dump(), "user_id": principal.user_id}
        try:
            check_finish(cfg, project_id, payload)
            job = queue.submit(
                "workflow_finish",
                project_id,
                payload,
                f"{principal.user_id}:finish:{idempotency_key}",
            )
            return job.model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/v1/projects/{project_id}/analysis", dependencies=[Depends(_verify_token)])
    def get_analysis(project_id: str, compact: bool = False):
        from noir.analysis.analyzer import AnalysisService

        result = AnalysisService(cfg).get_analysis(project_id)
        if not result:
            raise HTTPException(404, "Analysis not found")
        # The phone inventory needs metadata, not tens of thousands of indexed methods.
        excluded = (
            {"smali_classes", "apktool_metadata", "input_cert_info", "assets"} if compact else set()
        )
        return result.model_dump(mode="json", exclude=excluded)

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

    @app.get("/v1/projects/{project_id}/plans/{plan_id}", dependencies=[Depends(_verify_token)])
    def get_plan(project_id: str, plan_id: str):
        from noir.application.patch_service import PlanService

        plan = PlanService(cfg).get_plan(plan_id)
        if not plan or plan.project_id != project_id:
            raise HTTPException(404, "Plan not found")
        data = plan.model_dump(mode="json")
        data["plan_hash"] = plan.compute_hash()
        data["review"] = review_state(
            project_id, plan.compute_hash(), plan.workspace_revision, "plan"
        )
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

    @app.get("/v1/projects/{project_id}/patches/{patch_id}", dependencies=[Depends(_verify_token)])
    def get_patch(project_id: str, patch_id: str):
        from noir.application.patch_service import PatchService
        from noir.infrastructure.database.repositories import PatchRepository

        patch = PatchService(cfg).get_patch(patch_id)
        if not patch or patch.project_id != project_id:
            raise HTTPException(404, "Patch not found")
        data = patch.model_dump(mode="json")
        data["patch_hash"] = patch.compute_hash()
        data["review"] = review_state(
            project_id, patch.compute_hash(), patch.workspace_revision, "patch"
        )
        data["applied"] = PatchRepository().is_applied(patch_id)
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
        project_id: str,
        principal: Principal,
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        from noir.infrastructure.database.repositories import ProjectRepository

        project = ProjectRepository().get(project_id)
        if not project:
            raise HTTPException(404, "Project not found")
        if idempotency_key:
            idempotency_key = f"{principal.user_id}:{idempotency_key}"
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
    def sign_project(project_id: str, req: SignRequest, principal: Principal):
        if not req.confirm:
            raise HTTPException(400, "Signing requires confirm=true")
        if not AccessService().owns_signer(principal.user_id, req.profile):
            raise HTTPException(404, "Signing profile not found")
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
    def download_artifact(
        project_id: str,
        build_id: str,
        principal: Principal,
        artifact: str = "signed",
    ):
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

        if artifact == "signed" and build.signed_apk_hash:
            from noir.infrastructure.artifacts import ArtifactStore, ArtifactStoreError

            try:
                url = ArtifactStore(cfg).signed_download_url(
                    principal.user_id,
                    project_id,
                    build_id,
                    expected_sha256=build.signed_apk_hash,
                    filename=f"noir-{project_id}-{build_id}-signed.apk",
                )
            except ArtifactStoreError as exc:
                raise HTTPException(503, str(exc)) from exc
            if url:
                return RedirectResponse(url=url, status_code=307)

        return FileResponse(apk_path, filename=Path(apk_path).name)

    # ── Jobs ──────────────────────────────────────────────────────

    @app.get("/v1/jobs", dependencies=[Depends(_verify_token)])
    def list_jobs(principal: Principal, project_id: str | None = Query(None)):
        from noir.infrastructure.database.repositories import JobRepository

        repo = JobRepository()
        if project_id and not AccessService().owns_project(principal.user_id, project_id):
            raise HTTPException(404, "Project not found")
        jobs = (
            repo.list_by_project(project_id)
            if project_id
            else repo.list_all(user_id=principal.user_id)
        )
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
    def list_signing_profiles(principal: Principal):
        from noir.infrastructure.database.repositories import SigningProfileRepository

        profiles = SigningProfileRepository().list_all(user_id=principal.user_id)
        return {"profiles": [{"name": p.name, "type": p.profile_type.value} for p in profiles]}

    @app.post("/v1/keys/personal")
    def provision_personal_signer(principal: Principal):
        profile = AccessService().ensure_personal_signer(cfg, principal.user_id)
        return {"name": profile.name, "type": profile.profile_type.value}

    @app.post("/v1/jobs/{job_id}/cancel", dependencies=[Depends(_verify_token)])
    def cancel_job(job_id: str):
        return queue.cancel(job_id).model_dump(mode="json")

    @app.get("/v1/jobs/{job_id}/events", dependencies=[Depends(_verify_token)])
    async def stream_job_events(job_id: str, after: str | None = None):
        from noir.application.jobs import TERMINAL
        from noir.infrastructure.database.repositories import EventRepository, JobRepository

        if not JobRepository().get(job_id):
            raise HTTPException(404, "Job not found")

        async def events():
            cursor = after
            if after is not None:
                event = EventRepository().get(after)
                if event is None or event.job_id != job_id:
                    yield 'event: error\ndata: {"error":"Unknown event cursor"}\n\n'
                    return
            while True:
                batch = EventRepository().list_by_job(job_id, after_id=cursor, limit=100)
                for event in batch:
                    cursor = event.event_id
                    yield f"id: {event.event_id}\ndata: {event.model_dump_json()}\n\n"
                if len(batch) == 100:
                    continue
                job = JobRepository().get(job_id)
                if not job or job.state in TERMINAL:
                    data = job.model_dump_json() if job else "{}"
                    yield f"event: done\ndata: {data}\n\n"
                    return
                await asyncio.sleep(0.5)

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
