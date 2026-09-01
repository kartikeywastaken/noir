"""Shared, approval-gated AI use cases for CLI and HTTP."""

from __future__ import annotations

import re
from difflib import get_close_matches

from noir.analysis.analyzer import AnalysisService
from noir.application.patch_service import PatchService, PlanService, PlanServiceError
from noir.domain.enums import ApprovalScope, PatchOperationType
from noir.infrastructure.ai.context import AiContextTools
from noir.infrastructure.ai.gemini import GeminiProvider
from noir.infrastructure.database.repositories import ApprovalRepository, ProjectRepository
from noir.infrastructure.filesystem.workspace import ProjectWorkspace, WorkspaceError
from noir.security.locking import project_lock, require_clean_workspace


def _invalid_plan_paths(
    plan, workspace: ProjectWorkspace, allowed_paths: set[str] | None = None
) -> list[str]:
    invalid: list[str] = []
    for change in plan.file_changes:
        if change.operation == PatchOperationType.CREATE_FILE:
            continue
        if allowed_paths is not None and change.relative_path not in allowed_paths:
            invalid.append(change.relative_path)
            continue
        try:
            if not workspace.safe_path(change.relative_path).is_file():
                invalid.append(change.relative_path)
        except (OSError, ValueError, WorkspaceError):
            invalid.append(change.relative_path)
    return invalid


def _grounding_feedback(missing: list[str], real_files: list[str]) -> str:
    """Give the model a compact correction using only paths proven to exist."""
    lines = [
        "Your previous plan referenced paths that do not exist. Produce a corrected plan using "
        "only exact paths from the supplied files inventory. Do not repeat these invalid paths:"
    ]
    lower_files = [(path, path.lower()) for path in real_files]
    for path in missing:
        basename = path.rsplit("/", 1)[-1].lower()
        stem_tokens = set(re.findall(r"[a-z0-9]{3,}", basename))
        related = [
            candidate
            for candidate, lower in lower_files
            if basename == lower.rsplit("/", 1)[-1] or any(token in lower for token in stem_tokens)
        ][:8]
        if not related:
            related = get_close_matches(path, real_files, n=5, cutoff=0.25)
        lines.append(f"- invalid: {path}")
        if related:
            lines.append(f"  nearby real paths: {', '.join(related)}")
    lines.append(
        "If no listed path provides enough evidence for the requested behavior, explain that in "
        "unsupported_aspects instead of inventing a file."
    )
    return "\n".join(lines)[:12_000]


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
        workspace = ProjectWorkspace(project_id, config)
        context_tools = AiContextTools(workspace, analysis)
        context = context_tools.build_context(user_request=request)
        provider = GeminiProvider(config=config)
        plan = provider.generate_plan(request, analysis, context, project_id=project_id)
        # The human-readable inventory is independently byte-bounded and can omit a
        # binary that was deliberately selected for structured inspection. Evidence
        # paths are equally host-grounded, and _invalid_plan_paths still verifies the
        # target is a real file inside the decoded workspace.
        allowed_paths = (
            set(context["files"])
            | set(context["file_snippets"])
            | set(context["binary_inspection"])
        )
        invalid = _invalid_plan_paths(plan, workspace, allowed_paths)
        if invalid:
            context["planning_feedback"] = _grounding_feedback(invalid, sorted(allowed_paths))
            plan = provider.generate_plan(request, analysis, context, project_id=project_id)
            invalid = _invalid_plan_paths(plan, workspace, allowed_paths)
        if invalid:
            missing = ", ".join(invalid[:10])
            raise PlanServiceError(
                "AI proposed files that do not exist after one automatic grounded correction: "
                f"{missing}. No plan was saved."
            )
        return PlanService(config).create_plan(plan)


def generate_patch(config, project_id, plan_id, *, preview=False, analysis=None):
    with project_lock(config, project_id):
        project = ProjectRepository().get(project_id)
        plan = PlanService(config).get_plan(plan_id)
        if not project or not plan or plan.project_id != project_id:
            raise PlanServiceError("Plan not found in this project")
        if not plan.file_changes:
            raise PlanServiceError(
                "This plan contains no safe, supported file changes. Revise the request instead "
                "of generating a patch."
            )
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
