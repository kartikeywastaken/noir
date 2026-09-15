"""Shared, approval-gated AI use cases for CLI and HTTP."""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from collections import OrderedDict
from difflib import get_close_matches
from typing import Any

from noir.analysis.analyzer import AnalysisService
from noir.application.patch_service import PatchService, PlanService, PlanServiceError
from noir.domain.enums import ApprovalScope, PatchOperationType
from noir.infrastructure.ai.context import AiContextTools
from noir.infrastructure.ai.gemini import GeminiProvider
from noir.infrastructure.database.repositories import ApprovalRepository, ProjectRepository
from noir.infrastructure.filesystem.workspace import ProjectWorkspace, WorkspaceError
from noir.security.locking import project_lock, require_clean_workspace

logger = logging.getLogger(__name__)

# ── Workflow call budget ─────────────────────────────────────────────


class _WorkflowCallBudget:
    """Tracks logical planning calls across discovery + planning + correction.

    Provider retries are controlled separately by the Gemini retry settings.
    This budget bounds the calls deliberately initiated by one plan workflow.
    """

    def __init__(self, config):
        self.limit = config.ai_max_workflow_calls
        self.used = 0

    def consume(self, n: int = 1, *, label: str = "AI call") -> None:
        self.used += n
        if self.used > self.limit:
            raise PlanServiceError(
                f"Workflow call budget exhausted ({self.used}/{self.limit}). "
                f"Last: {label}. Reduce discovery_max_rounds or ai_max_workflow_calls, "
                f"or simplify the request."
            )


# ── Plan result cache ────────────────────────────────────────────────

_PLAN_CACHE_MAX = 32


class _PlanCache:
    """LRU cache for plan results, keyed on (project_id, revision, request_hash).

    When the user reopens "Preview changes" with the same request on the same
    workspace revision, this returns the stored plan instead of re-calling Gemini.
    A changed request or workspace revision invalidates the entry.
    """

    def __init__(self):
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.RLock()

    @staticmethod
    def _key(project_id: str, revision: int, request: str, model: str = "") -> str:
        request_hash = hashlib.sha256(f"{model}\0{request}".encode()).hexdigest()[:16]
        return f"{project_id}:{revision}:{request_hash}"

    def get(self, project_id: str, revision: int, request: str, model: str = ""):
        key = self._key(project_id, revision, request, model)
        with self._lock:
            plan = self._cache.get(key)
            if plan is not None:
                self._cache.move_to_end(key)
            return plan

    def put(self, project_id: str, revision: int, request: str, plan, model: str = "") -> None:
        key = self._key(project_id, revision, request, model)
        with self._lock:
            self._cache[key] = plan
            self._cache.move_to_end(key)
            while len(self._cache) > _PLAN_CACHE_MAX:
                self._cache.popitem(last=False)

    def invalidate(self, project_id: str) -> None:
        """Remove every cached plan for a project after an explicit rejection."""
        prefix = f"{project_id}:"
        with self._lock:
            for key in [key for key in self._cache if key.startswith(prefix)]:
                del self._cache[key]


_plan_cache = _PlanCache()


def invalidate_plan_cache(project_id: str) -> None:
    """Invalidate cached AI plans for a project."""
    _plan_cache.invalidate(project_id)


def _create_generation_provider(config, model: str | None = None):
    """Create the selected plan/patch provider without exposing provider credentials."""
    if model and model.startswith("openrouter:"):
        from noir.infrastructure.ai.openrouter import OpenRouterGenerationProvider

        return OpenRouterGenerationProvider(config, model=model.removeprefix("openrouter:"))
    return GeminiProvider(model=model, config=config, purpose="generation")


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


def _create_discovery_provider(config):
    """Factory: create the configured discovery provider."""
    provider_name = config.discovery_provider
    if provider_name == "openrouter":
        try:
            from noir.infrastructure.ai.openrouter import OpenRouterDiscoveryProvider

            return OpenRouterDiscoveryProvider(config)
        except Exception as exc:
            logger.warning("OpenRouter discovery unavailable (%s); falling back to local", exc)
            from noir.infrastructure.ai.local_discovery import LocalDiscoveryProvider

            return LocalDiscoveryProvider(config)
    elif provider_name == "gemini":
        # Gemini discovery requires the full EvidenceDiscovery wrapper — handled below.
        return None  # Signal caller to use the legacy Gemini path
    else:
        from noir.infrastructure.ai.local_discovery import LocalDiscoveryProvider

        return LocalDiscoveryProvider(config)


def generate_plan(config, project_id, request, consent, *, analysis=None, model=None):
    if not consent:
        raise PlanServiceError("Explicit AI upload consent required")
    with project_lock(config, project_id):
        require_clean_workspace(config, project_id)
        analysis = analysis or AnalysisService(config).analyze(
            project_id, ProjectWorkspace(project_id, config), persist=False
        )
        if not analysis:
            raise PlanServiceError("No analysis available")

        # Check the plan cache before making any API calls.
        project = ProjectRepository().get(project_id)
        revision = project.workspace_revision if project else 0
        selected_model = model or config.ai_model
        cached = _plan_cache.get(project_id, revision, request, selected_model)
        if cached is not None:
            logger.info("Plan cache hit for project=%s revision=%d", project_id, revision)
            return cached

        workspace = ProjectWorkspace(project_id, config)
        context_tools = AiContextTools(workspace, analysis)
        generation_provider = _create_generation_provider(config, selected_model)
        budget = _WorkflowCallBudget(config)

        # Phase C: evidence-driven discovery before plan generation.
        discovery_transcript: list[dict] = []
        discovery_api_calls = 0
        discovery_stop_reason = ""
        try:
            from noir.infrastructure.ai.discovery import build_discovered_context

            discovery_provider = _create_discovery_provider(config)

            if discovery_provider is not None:
                # OpenRouter or local provider — clean interface
                discovery = discovery_provider.discover(request, context_tools, analysis)
            else:
                # Legacy Gemini discovery path
                from noir.infrastructure.ai.discovery import EvidenceDiscovery

                gemini_discovery = GeminiProvider(config=config, purpose="discovery")
                discovery = EvidenceDiscovery(
                    gemini_discovery, context_tools, config, analysis
                ).discover(request)

            discovery_transcript = [r.to_dict() for r in discovery.transcript]
            discovery_api_calls = discovery.api_calls
            discovery_stop_reason = discovery.stop_reason
            budget.consume(discovery_api_calls, label="discovery")

            if not discovery.used_static_fallback:
                context = build_discovered_context(context_tools, discovery, user_request=request)
            else:
                context = context_tools.build_context(user_request=request)
        except PlanServiceError:
            raise  # budget exhaustion is a real error, not a fallback case
        except Exception:
            # Any discovery failure falls back to static selection (requirement #4).
            context = context_tools.build_context(user_request=request)

        budget.consume(1, label="plan generation")
        plan = generation_provider.generate_plan(request, analysis, context, project_id=project_id)
        plan.discovery_transcript = discovery_transcript
        plan.discovery_api_calls = discovery_api_calls
        plan.discovery_stop_reason = discovery_stop_reason

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
            budget.consume(1, label="grounding correction")
            context["planning_feedback"] = _grounding_feedback(invalid, sorted(allowed_paths))
            plan = generation_provider.generate_plan(
                request, analysis, context, project_id=project_id
            )
            plan.discovery_transcript = discovery_transcript
            plan.discovery_api_calls = discovery_api_calls
            plan.discovery_stop_reason = discovery_stop_reason
            invalid = _invalid_plan_paths(plan, workspace, allowed_paths)
        if invalid:
            missing = ", ".join(invalid[:10])
            raise PlanServiceError(
                "AI proposed files that do not exist after one automatic grounded correction: "
                f"{missing}. No plan was saved."
            )
        result = PlanService(config).create_plan(plan)
        _plan_cache.put(project_id, revision, request, result, selected_model)
        return result


def generate_patch(config, project_id, plan_id, *, preview=False, analysis=None, model=None):
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
        patch = _create_generation_provider(config, model).generate_patch(plan, context)
        return PatchService(config).store_patch(patch, preview=preview)
