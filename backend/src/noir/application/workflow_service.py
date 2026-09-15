"""Three-screen workflow: prepare an unapplied preview, explicitly approve, build/sign.

All slow work runs in persistent jobs. Preview generation grants no approval;
the user's combined confirmation is bound to both exact hashes and the revision.
"""

from noir.analysis.analyzer import AnalysisService
from noir.application.access_service import AccessService
from noir.application.ai_service import generate_patch, generate_plan
from noir.application.build_service import BuildService
from noir.application.patch_service import PatchService, PlanService
from noir.application.signing_service import SigningService
from noir.domain.enums import ApprovalScope, EventSeverity, WorkflowStage
from noir.domain.models import AuditEvent
from noir.infrastructure.database.repositories import (
    ApprovalRepository,
    BuildRepository,
    EventRepository,
    JobRepository,
    PatchRepository,
    ProjectRepository,
)
from noir.infrastructure.filesystem.workspace import ProjectWorkspace, compute_file_hash
from noir.infrastructure.processes.runner import CANCEL_CHECK
from noir.patches.engine import PatchEngine
from noir.security.locking import project_lock, require_clean_workspace


def _checkpoint(job, stage, **values):
    cancelled = CANCEL_CHECK.get()
    if cancelled and cancelled():
        raise ValueError("Operation cancelled; completed work is retained in History")
    job.stage = WorkflowStage(stage)
    job.result_data.update(values)
    JobRepository().update(job)
    EventRepository().create(
        AuditEvent(
            project_id=job.project_id,
            job_id=job.job_id,
            stage=job.stage,
            severity=EventSeverity.INFO,
            message=f"Workflow: {stage}",
        )
    )


def check_finish(config, project_id, payload):
    plan = PlanService(config).get_plan(payload["plan_id"])
    patch = PatchService(config).get_patch(payload["patch_id"])
    project = ProjectRepository().get(project_id)
    if (
        not project
        or not plan
        or not patch
        or plan.project_id != project_id
        or patch.project_id != project_id
        or patch.plan_id != plan.plan_id
    ):
        raise ValueError("Preview not found in this workspace")
    if (
        plan.compute_hash() != payload["plan_hash"]
        or patch.compute_hash() != payload["patch_hash"]
        or plan.workspace_revision != payload["revision"]
        or patch.workspace_revision != payload["revision"]
    ):
        raise ValueError("Preview changed. Review the current changes before approving")
    applied = PatchRepository().is_applied(patch.patch_id)
    expected_revision = payload["revision"] + int(applied)
    if project.workspace_revision != expected_revision or project.dirty:
        raise ValueError("Workspace changed. Generate a fresh preview")
    if not payload.get("confirm"):
        raise ValueError("Review and explicitly approve the changes and signing first")
    if applied:
        for scope, item in ((ApprovalScope.PLAN, plan), (ApprovalScope.PATCH, patch)):
            if not ApprovalRepository().find_valid(
                project_id, scope, item.compute_hash(), payload["revision"]
            ):
                raise ValueError("The earlier approval is no longer valid")
    return plan, patch, applied


def prepare(config, job):
    payload, project_id = job.result_data["payload"], job.project_id
    with project_lock(config, project_id):
        project = ProjectRepository().get(project_id)
        if not project or project.workspace_revision != payload["revision"]:
            raise ValueError("Workspace changed before preparation; create a new preview")
        require_clean_workspace(config, project_id)
        _checkpoint(job, "analyzing")
        # A single immutable, locked workspace analysis serves both AI calls.
        analysis = AnalysisService(config).analyze(
            project_id, ProjectWorkspace(project_id, config), persist=False
        )
        _checkpoint(job, "planning")
        plan = generate_plan(
            config,
            project_id,
            payload["user_request"],
            payload["allow_ai_upload"],
            analysis=analysis,
            model=payload.get("model"),
        )
        if not plan.file_changes:
            _checkpoint(job, "planning", plan_id=plan.plan_id, unsupported=True)
            return {"plan_id": plan.plan_id, "unsupported": True}
        _checkpoint(job, "generating_patch", plan_id=plan.plan_id)
        patch = generate_patch(
            config,
            project_id,
            plan.plan_id,
            preview=True,
            analysis=analysis,
            model=payload.get("model"),
        )
        _checkpoint(job, "generating_patch", patch_id=patch.patch_id)
        return {"plan_id": plan.plan_id, "patch_id": patch.patch_id}


def finish(config, job):
    payload, project_id = job.result_data["payload"], job.project_id
    with project_lock(config, project_id):
        plan, patch, applied = check_finish(config, project_id, payload)
        AccessService().user(payload["user_id"])
        require_clean_workspace(config, project_id)
        _checkpoint(job, "applying_patch")
        if not applied:
            # Validate the complete deterministic preview BEFORE recording approvals.
            PatchEngine(ProjectWorkspace(project_id, config)).generate_diff(patch)
            actor = f"user:{payload['user_id']}:combined_review"
            PlanService(config).approve_plan(project_id, plan.plan_id, plan.compute_hash(), actor)
            PatchService(config).approve_patch(
                project_id, patch.patch_id, patch.compute_hash(), actor
            )
            result = PatchService(config).apply_patch(project_id, patch.patch_id)
            if not result["validation"]["passed"]:
                raise ValueError(
                    "Patched workspace failed validation. Inspect the audit; no APK signed"
                )
        _checkpoint(job, "rebuilding")
        # Explicit retry can resume a successful build/signing checkpoint, never reapply edits.
        build = None
        for earlier in JobRepository().list_by_project(project_id):
            data = earlier.result_data
            if (
                data.get("operation") == "workflow_finish"
                and data.get("payload", {}).get("patch_hash") == patch.compute_hash()
                and data.get("build_id")
            ):
                candidate = BuildRepository().get(data["build_id"])
                if (
                    candidate
                    and candidate.success
                    and candidate.project_id == project_id
                    and candidate.workspace_revision == patch.workspace_revision + 1
                ):
                    build = candidate
                    break
        if build is None:
            build = BuildService(config).build(project_id, job=job)
            if not build.success:
                raise ValueError(build.error_message or "APK rebuild failed")
        _checkpoint(job, "signing", build_id=build.build_id)
        if not build.signed_apk_hash:
            profile = AccessService().ensure_personal_signer(config, payload["user_id"])
            build = SigningService(config).sign(
                project_id, build.build_id, profile.name, confirmed=True
            )
        _checkpoint(job, "verifying")
        from pathlib import Path

        from noir.infrastructure.android_tools.tools import verify_signature

        signed = Path(build.signed_apk_path)
        if compute_file_hash(signed) != build.signed_apk_hash:
            raise ValueError("Signed APK hash changed; download blocked")
        verification = verify_signature(config, signed)
        if not verification.get("verified"):
            raise ValueError("Signed APK verification failed; download blocked")
        _checkpoint(job, "reporting")
        from noir.auditing.reporter import AuditReporter

        AuditReporter(config).save_reports(project_id)
        return {"build_id": build.build_id}
