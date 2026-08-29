"""Shared, approval-gated AI use cases for CLI and HTTP."""

from noir.analysis.analyzer import AnalysisService
from noir.application.patch_service import PatchService, PlanService, PlanServiceError
from noir.domain.enums import ApprovalScope
from noir.infrastructure.ai.context import AiContextTools
from noir.infrastructure.ai.gemini import GeminiProvider
from noir.infrastructure.database.repositories import ApprovalRepository, ProjectRepository
from noir.infrastructure.filesystem.workspace import ProjectWorkspace
from noir.security.locking import project_lock, require_clean_workspace


def generate_plan(config, project_id, request, consent, *, analysis=None):
    if not consent:
        raise PlanServiceError("Explicit AI upload consent required")
    with project_lock(config, project_id):
        require_clean_workspace(config, project_id)
        analysis = analysis or AnalysisService(config).analyze(
            project_id, ProjectWorkspace(project_id, config), persist=False
        )
        if not analysis:
            raise PlanServiceError("No analysis available")
        context = AiContextTools(ProjectWorkspace(project_id, config), analysis).build_context(
            user_request=request
        )
        plan = GeminiProvider(config=config).generate_plan(
            request, analysis, context, project_id=project_id
        )
        return PlanService(config).create_plan(plan)


def generate_patch(config, project_id, plan_id, *, preview=False, analysis=None):
    with project_lock(config, project_id):
        project = ProjectRepository().get(project_id)
        plan = PlanService(config).get_plan(plan_id)
        if not project or not plan or plan.project_id != project_id:
            raise PlanServiceError("Plan not found in this project")
        if not preview and not ApprovalRepository().find_valid(
            project_id, ApprovalScope.PLAN, plan.compute_hash(), project.workspace_revision
        ):
            raise PlanServiceError("Approve the plan before any AI patch request")
        require_clean_workspace(config, project_id)
        analysis = analysis or AnalysisService(config).analyze(
            project_id, ProjectWorkspace(project_id, config), persist=False
        )
        context = AiContextTools(ProjectWorkspace(project_id, config), analysis).build_context(
            [change.relative_path for change in plan.file_changes], user_request=plan.user_request
        )
        patch = GeminiProvider(config=config).generate_patch(plan, context)
        return PatchService(config).store_patch(patch, preview=preview)
