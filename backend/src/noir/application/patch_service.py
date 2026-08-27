"""Plan and patch service — orchestrates change plans, approvals, and patch application."""

from __future__ import annotations

from noir.domain.config import NoirConfig, get_config
from noir.domain.enums import (
    ApprovalScope,
    EventSeverity,
    Provenance,
    WorkflowStage,
)
from noir.domain.models import (
    ApprovalRecord,
    AuditEvent,
    ChangePlan,
    PatchSet,
)
from noir.infrastructure.database.repositories import (
    ApprovalRepository,
    EventRepository,
    FileManifestRepository,
    PatchRepository,
    PlanRepository,
    ProjectRepository,
)
from noir.infrastructure.filesystem.workspace import ProjectWorkspace
from noir.patches.engine import PatchEngine
from noir.security.locking import locked_project, require_clean_workspace


class PlanServiceError(Exception):
    pass


class PlanService:
    """Manages change plans and their approval lifecycle."""

    def __init__(self, config: NoirConfig | None = None):
        self.config = config or get_config()
        self.project_repo = ProjectRepository()
        self.plan_repo = PlanRepository()
        self.approval_repo = ApprovalRepository()
        self.event_repo = EventRepository()

    @locked_project
    def create_plan(self, plan: ChangePlan) -> ChangePlan:
        """Store a new change plan."""
        project = self.project_repo.get(plan.project_id)
        if not project:
            raise PlanServiceError(f"Project not found: {plan.project_id}")
        plan.workspace_revision = project.workspace_revision
        self.plan_repo.create(plan)
        self.event_repo.create(
            AuditEvent(
                project_id=plan.project_id,
                stage=WorkflowStage.PLANNING,
                severity=EventSeverity.INFO,
                message=f"Change plan created: {plan.plan_id}",
                metadata={"plan_hash": plan.compute_hash()},
            )
        )
        return plan

    def get_plan(self, plan_id: str) -> ChangePlan | None:
        return self.plan_repo.get(plan_id)

    def list_plans(self, project_id: str) -> list[ChangePlan]:
        return self.plan_repo.list_by_project(project_id)

    @locked_project
    def approve_plan(
        self, project_id: str, plan_id: str, expected_hash: str, actor: str = "local_cli"
    ) -> ApprovalRecord:
        """Approve a plan by its hash."""
        project = self.project_repo.get(project_id)
        if not project:
            raise PlanServiceError(f"Project not found: {project_id}")

        plan = self.plan_repo.get(plan_id)
        if not plan or plan.project_id != project_id:
            raise PlanServiceError(f"Plan not found: {plan_id}")

        actual_hash = plan.compute_hash()
        if actual_hash != expected_hash:
            raise PlanServiceError(
                f"Plan hash mismatch: expected {expected_hash[:16]}..., got {actual_hash[:16]}..."
            )

        if plan.workspace_revision != project.workspace_revision:
            raise PlanServiceError(
                f"Plan is stale: plan revision {plan.workspace_revision}, "
                f"current revision {project.workspace_revision}"
            )

        approval = ApprovalRecord(
            project_id=project_id,
            scope=ApprovalScope.PLAN,
            workspace_revision=project.workspace_revision,
            target_hash=actual_hash,
            target_id=plan_id,
            actor=actor,
        )
        self.approval_repo.create(approval)

        self.event_repo.create(
            AuditEvent(
                project_id=project_id,
                stage=WorkflowStage.PLANNING,
                severity=EventSeverity.INFO,
                message=f"Plan approved: {plan_id}",
                metadata={"approval_id": approval.approval_id, "plan_hash": actual_hash},
            )
        )

        return approval

    @locked_project
    def reject_plan(self, project_id: str, plan_id: str) -> None:
        """Reject a plan."""
        plan = self.plan_repo.get(plan_id)
        if not plan or plan.project_id != project_id:
            raise PlanServiceError("Plan not found in this project")
        self.approval_repo.invalidate_for_project(project_id, ApprovalScope.PLAN)
        self.approval_repo.invalidate_for_project(project_id, ApprovalScope.PATCH)
        self.event_repo.create(
            AuditEvent(
                project_id=project_id,
                stage=WorkflowStage.PLANNING,
                severity=EventSeverity.INFO,
                message=f"Plan rejected: {plan_id}",
            )
        )


class PatchService:
    """Manages patch generation, approval, and application."""

    def __init__(self, config: NoirConfig | None = None):
        self.config = config or get_config()
        self.project_repo = ProjectRepository()
        self.plan_repo = PlanRepository()
        self.patch_repo = PatchRepository()
        self.approval_repo = ApprovalRepository()
        self.event_repo = EventRepository()
        self.manifest_repo = FileManifestRepository()

    @locked_project
    def store_patch(self, patch: PatchSet) -> PatchSet:
        """Store a new patch set."""
        project = self.project_repo.get(patch.project_id)
        if not project:
            raise PlanServiceError(f"Project not found: {patch.project_id}")

        plan = self.plan_repo.get(patch.plan_id)
        if not plan or plan.project_id != patch.project_id:
            raise PlanServiceError("Patch plan does not belong to this project")
        if patch.workspace_revision != project.workspace_revision:
            raise PlanServiceError("Patch was generated against a stale revision")
        allowed = {(change.relative_path, change.operation) for change in plan.file_changes}
        if any((op.relative_path, op.operation) not in allowed for op in patch.operations):
            raise PlanServiceError("Patch operations exceed the approved plan")
        # Verify plan is approved
        plan_approval = self.approval_repo.find_valid(
            patch.project_id,
            ApprovalScope.PLAN,
            self.plan_repo.get_hash(patch.plan_id) or "",
            project.workspace_revision,
        )
        if not plan_approval and patch.provenance == Provenance.AI_GENERATED:
            raise PlanServiceError("Plan must be approved before generating patches")

        workspace = ProjectWorkspace(patch.project_id, self.config)
        for op in patch.operations:
            target = workspace.safe_path(op.relative_path)
            if target.exists():
                from noir.infrastructure.filesystem.workspace import compute_file_hash

                actual = compute_file_hash(target)
                if op.expected_preimage_hash and op.expected_preimage_hash != actual:
                    raise PlanServiceError("Patch preimage does not match current file")
                op.expected_preimage_hash = actual
            else:
                op.expected_absent = True
        PatchEngine(workspace).generate_diff(patch)
        self.patch_repo.create(patch)

        self.event_repo.create(
            AuditEvent(
                project_id=patch.project_id,
                stage=WorkflowStage.GENERATING_PATCH,
                severity=EventSeverity.INFO,
                message=f"Patch generated: {patch.patch_id}",
                metadata={"patch_hash": patch.compute_hash()},
            )
        )

        return patch

    def get_patch(self, patch_id: str) -> PatchSet | None:
        return self.patch_repo.get(patch_id)

    def list_patches(self, project_id: str) -> list[PatchSet]:
        return self.patch_repo.list_by_project(project_id)

    @locked_project
    def approve_patch(
        self, project_id: str, patch_id: str, expected_hash: str, actor: str = "local_cli"
    ) -> ApprovalRecord:
        """Approve a patch by its hash."""
        project = self.project_repo.get(project_id)
        if not project:
            raise PlanServiceError(f"Project not found: {project_id}")

        patch = self.patch_repo.get(patch_id)
        if not patch or patch.project_id != project_id:
            raise PlanServiceError(f"Patch not found: {patch_id}")

        actual_hash = patch.compute_hash()
        if actual_hash != expected_hash:
            raise PlanServiceError(
                f"Patch hash mismatch: expected {expected_hash[:16]}..., got {actual_hash[:16]}..."
            )

        if patch.workspace_revision != project.workspace_revision:
            raise PlanServiceError(
                f"Patch is stale: patch revision {patch.workspace_revision}, "
                f"current revision {project.workspace_revision}"
            )

        approval = ApprovalRecord(
            project_id=project_id,
            scope=ApprovalScope.PATCH,
            workspace_revision=project.workspace_revision,
            target_hash=actual_hash,
            target_id=patch_id,
            actor=actor,
        )
        self.approval_repo.create(approval)

        self.event_repo.create(
            AuditEvent(
                project_id=project_id,
                stage=WorkflowStage.GENERATING_PATCH,
                severity=EventSeverity.INFO,
                message=f"Patch approved: {patch_id}",
                metadata={"approval_id": approval.approval_id, "patch_hash": actual_hash},
            )
        )

        return approval

    @locked_project
    def apply_patch(self, project_id: str, patch_id: str) -> dict:
        """Apply an approved patch to the workspace."""
        project = self.project_repo.get(project_id)
        if not project:
            raise PlanServiceError(f"Project not found: {project_id}")

        patch = self.patch_repo.get(patch_id)
        if not patch or patch.project_id != project_id:
            raise PlanServiceError(f"Patch not found: {patch_id}")

        require_clean_workspace(self.config, project_id)
        plan = self.plan_repo.get(patch.plan_id)
        if not plan or not self.approval_repo.find_valid(
            project_id, ApprovalScope.PLAN, plan.compute_hash(), project.workspace_revision
        ):
            raise PlanServiceError("Plan approval is missing or stale")

        if self.patch_repo.is_applied(patch_id):
            raise PlanServiceError(f"Patch already applied: {patch_id}")

        # Verify approval exists
        patch_hash = patch.compute_hash()
        approval = self.approval_repo.find_valid(
            project_id, ApprovalScope.PATCH, patch_hash, project.workspace_revision
        )
        if not approval:
            raise PlanServiceError(
                "Patch must be approved before application. "
                f"Approve with: noir patch approve {project_id} {patch_id} --hash {patch_hash}"
            )

        workspace = ProjectWorkspace(project_id, self.config)
        engine = PatchEngine(workspace)

        # Apply
        result = engine.apply_patch(patch)

        # Advance revision
        new_revision = project.workspace_revision + 1
        project.workspace_revision = new_revision
        self.project_repo.update(project)

        # Save new manifest
        new_manifest = workspace.build_file_manifest()
        self.manifest_repo.save(project_id, new_revision, new_manifest)

        # Mark as applied
        self.patch_repo.mark_applied(patch_id)

        # Invalidate downstream state
        self.approval_repo.invalidate_for_project(project_id, ApprovalScope.SIGNING)
        from noir.validation.workspace_validator import ValidationService

        validation = ValidationService(self.config).validate(project_id)
        result["validation"] = validation.model_dump(mode="json")

        self.event_repo.create(
            AuditEvent(
                project_id=project_id,
                stage=WorkflowStage.APPLYING_PATCH,
                severity=EventSeverity.INFO,
                message=f"Patch applied: {patch_id}, workspace revision {new_revision}",
                metadata={
                    "patch_id": patch_id,
                    "workspace_revision": new_revision,
                    "operations_applied": result.get("operations_applied", 0),
                },
            )
        )

        return result

    @locked_project
    def undo_patch(self, project_id: str, patch_id: str) -> dict:
        """Undo a previously applied patch."""
        project = self.project_repo.get(project_id)
        if not project:
            raise PlanServiceError(f"Project not found: {project_id}")

        patch = self.patch_repo.get(patch_id)
        if not patch or patch.project_id != project_id:
            raise PlanServiceError(f"Patch not found: {patch_id}")

        if not self.patch_repo.is_applied(patch_id):
            raise PlanServiceError(f"Patch is not applied: {patch_id}")

        require_clean_workspace(self.config, project_id)
        workspace = ProjectWorkspace(project_id, self.config)
        engine = PatchEngine(workspace)
        result = engine.undo_patch(patch)

        self.patch_repo.mark_undone(patch_id)
        self.approval_repo.invalidate_for_project(project_id, ApprovalScope.SIGNING)

        new_revision = project.workspace_revision + 1
        project.workspace_revision = new_revision
        self.project_repo.update(project)

        new_manifest = workspace.build_file_manifest()
        self.manifest_repo.save(project_id, new_revision, new_manifest)

        self.event_repo.create(
            AuditEvent(
                project_id=project_id,
                stage=WorkflowStage.APPLYING_PATCH,
                severity=EventSeverity.INFO,
                message=f"Patch undone: {patch_id}",
            )
        )

        return result

    def show_diff(self, project_id: str, patch_id: str) -> list[dict]:
        """Show diff preview for a patch."""
        patch = self.patch_repo.get(patch_id)
        if not patch or patch.project_id != project_id:
            raise PlanServiceError(f"Patch not found: {patch_id}")

        workspace = ProjectWorkspace(project_id, self.config)
        engine = PatchEngine(workspace)
        return engine.generate_diff(patch)
