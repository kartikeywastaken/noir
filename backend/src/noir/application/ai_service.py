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
from noir.infrastructure.ai.factory import create_ai_provider
from noir.infrastructure.ai.gemini import GeminiProvider
from noir.infrastructure.database.repositories import ApprovalRepository, ProjectRepository
from noir.infrastructure.filesystem.workspace import ProjectWorkspace, WorkspaceError
from noir.security.locking import project_lock, require_clean_workspace

logger = logging.getLogger(__name__)


_EXECUTABLE_PLAN_OPERATIONS = {
    PatchOperationType.SMALI_REPLACE_METHOD,
    PatchOperationType.SMALI_INSERT_AT_ANCHOR,
    PatchOperationType.CIL_REPLACE_METHOD_BODY,
    PatchOperationType.CIL_INSERT_METHOD,
    PatchOperationType.CIL_REPLACE_FIELD_INIT,
    PatchOperationType.IL2CPP_FORCE_RETURN,
    PatchOperationType.IL2CPP_NOP_RANGE,
    PatchOperationType.NATIVE_BYTE_PATCH,
    PatchOperationType.NATIVE_NOP_RANGE,
    PatchOperationType.NATIVE_BRANCH_REDIRECT,
    PatchOperationType.DEX_STRING_PATCH,
    PatchOperationType.DEX_BYTE_PATCH,
}


def _request_requirements(request: str) -> dict[str, str]:
    """Extract high-risk semantics that must not disappear from a compound request.

    Intent routing is deliberately finite. These requirements cover stateful
    triggers and device-data requests that the reusable deterministic runtime
    cannot implement by reducing them to a plain launch action.
    """
    normalized = " ".join(request.strip().split())
    requirements: dict[str, str] = {}
    if re.search(
        r"(?i)\b(?:once|one[ -]?time|first(?:\s+time)?)\b[^.!?;]{0,100}"
        r"\b(?:after|on|when|upon)?\s*(?:login|log\s*in|sign[- ]?in)\b|"
        r"\b(?:login|log\s*in|sign[- ]?in)\b[^.!?;]{0,100}"
        r"\b(?:once|one[ -]?time|first(?:\s+time)?)\b",
        normalized,
    ):
        requirements["trigger_once_after_login"] = (
            "run only once after the user logs in, not on every application launch"
        )

    data_action = (
        r"(?:collect|send|post|provide|transmit|report|include|upload|capture|read|"
        r"retrieve|obtain|share)"
    )
    category_patterns = {
        "android_version": r"android\s+(?:os\s+)?version",
        "service_carrier": r"(?:service\s+)?carrier|network\s+operator|mobile\s+operator",
        "location": r"(?:device\s+|user\s+)?location|gps",
        "battery_status": r"battery(?:\s+(?:status|level|state))?",
        "device_information": r"device\s+info(?:rmation)?",
    }
    for category, pattern in category_patterns.items():
        if re.search(
            rf"(?i)\b{data_action}\b[^.!?;]{{0,260}}\b(?:{pattern})\b|"
            rf"\b(?:{pattern})\b[^.!?;]{{0,260}}\b{data_action}\b",
            normalized,
        ):
            requirements[f"data_{category}"] = (
                f"collect or transmit requested device data: {category.replace('_', ' ')}"
            )
    return requirements


def _is_executable_plan_change(change) -> bool:
    """Return whether a planned binding can change runtime behavior."""
    if change.operation in _EXECUTABLE_PLAN_OPERATIONS:
        return True
    suffix = change.relative_path.lower().rsplit(".", 1)[-1]
    return suffix in {"dex", "smali", "dll", "so"} and change.operation in {
        PatchOperationType.CREATE_FILE,
        PatchOperationType.REPLACE_FILE,
        PatchOperationType.REPLACE_BLOCK,
    }


def _plan_coverage_gaps(
    plan,
    detected_intents: list[str],
    request_requirements: dict[str, str] | None = None,
) -> list[str]:
    """Find requested or claimed behavior with no capable authorized edit.

    This structural check does not claim that an operation works. It prevents a
    resource-only patch from claiming executable behavior and leaves runtime
    verification to the later APK verification gates.
    """
    if plan.unsupported_aspects:
        # Any disclosed residual stops patch generation in the workflow. Do not
        # pretend a textual heuristic can improve on an explicit limitation.
        return []

    changes = list(plan.file_changes)
    has_executable = any(_is_executable_plan_change(change) for change in changes)
    has_smali = any(
        change.operation
        in {
            PatchOperationType.SMALI_REPLACE_METHOD,
            PatchOperationType.SMALI_INSERT_AT_ANCHOR,
        }
        for change in changes
    )
    has_manifest = any(change.relative_path == "AndroidManifest.xml" for change in changes)
    has_resource = any(
        change.relative_path.startswith(("res/", "resources/"))
        and change.relative_path.lower().endswith(".xml")
        for change in changes
    )
    normalized_triggers = " ".join(plan.runtime_triggers).lower().replace("-", " ")
    normalized_data = " ".join(plan.data_categories).lower().replace("-", " ")

    gaps: list[str] = []
    for intent in dict.fromkeys(detected_intents):
        covered = True
        if intent == "app_name":
            covered = has_manifest or has_resource
        elif intent == "permission":
            covered = has_manifest
        elif intent == "ui_layout":
            covered = has_resource
        elif intent in {"network_ping", "toast_flash"}:
            covered = has_executable
        elif intent == "receiver_service":
            covered = has_manifest or has_executable
        elif intent in {
            "react_native_js",
            "flutter_dart",
            "unity_mono",
            "unity_il2cpp",
            "native_elf",
            "xamarin_dotnet",
        }:
            covered = has_executable
        if not covered:
            gaps.append(
                f"requested intent {intent!r} has no authorized file change capable of implementing it"
            )

    if plan.network_destinations and not has_executable:
        gaps.append("declared network behavior has no executable Smali, DEX, managed, or native edit")
    if plan.data_categories and not has_executable:
        gaps.append("declared data collection has no executable Smali, DEX, managed, or native edit")
    if plan.smali_integration_points and not has_smali:
        gaps.append("declared Smali integration points have no authorized Smali operation")
    if plan.component_changes and not has_manifest:
        gaps.append("declared Android component changes have no AndroidManifest.xml operation")
    if (plan.runtime_triggers or plan.background_behavior) and not has_executable:
        gaps.append("declared runtime/background behavior has no executable file change")
    for requirement_id, description in (request_requirements or {}).items():
        if requirement_id == "trigger_once_after_login":
            has_login = bool(re.search(r"\b(?:login|log in|sign in)\b", normalized_triggers))
            has_once = bool(re.search(r"\b(?:once|one time|first time)\b", normalized_triggers))
            if not (has_executable and has_login and has_once):
                gaps.append(f"requested behavior is unaccounted for: {description}")
            continue
        if requirement_id.startswith("data_"):
            category = requirement_id.removeprefix("data_").replace("_", " ")
            aliases = {
                "android version": ("android version", "os version"),
                "service carrier": ("service carrier", "carrier", "network operator"),
                "battery status": ("battery status", "battery level", "battery state", "battery"),
                "device information": ("device information", "device info"),
            }.get(category, (category,))
            if not (has_executable and any(alias in normalized_data for alias in aliases)):
                gaps.append(f"requested behavior is unaccounted for: {description}")
    return list(dict.fromkeys(gaps))


def _coverage_feedback(gaps: list[str]) -> str:
    lines = [
        "Your previous plan was semantically incomplete. A disclosure or intended outcome is not "
        "an implementation. For every item below, either add an exact evidence-backed file change "
        "whose operation can implement it, or list that item explicitly in unsupported_aspects. "
        "Do not retain a claimed behavior without one of those outcomes:"
    ]
    lines.extend(f"- {gap}" for gap in gaps)
    return "\n".join(lines)[:12_000]

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
    """Keep the legacy Gemini seam while enabling ADK on the test branch.

    If the caller passes a model string prefixed with "openrouter:" (e.g.
    "openrouter:nvidia/nemotron-3.5-lightning:free") we swap both the
    generation provider AND the discovery provider to OpenRouter so the
    end-to-end flow is consistent with the user's selection.
    """
    if model and model.startswith("openrouter:"):
        or_model = model[len("openrouter:") :]
        from noir.infrastructure.ai.openrouter import OpenRouterGenerationProvider

        return OpenRouterGenerationProvider(model=or_model, config=config)
    if config.ai_provider == "adk":
        selected_config = config.model_copy(update={"ai_model": model}) if model else config
        return create_ai_provider(selected_config, purpose="generation")
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
    elif provider_name == "adk":
        from noir.infrastructure.ai.adk import AdkDiscoveryProvider

        return AdkDiscoveryProvider(config)
    else:
        from noir.infrastructure.ai.local_discovery import LocalDiscoveryProvider

        return LocalDiscoveryProvider(config)


def _apply_strategy_metadata(plan, detected_intents: list[str]):
    """Describe how an advanced plan reaches the existing APK.

    The deterministic runtime path sets this metadata directly. Advanced plans
    are grounded in model-selected files, so derive the strategy only from the
    approved plan shape instead of claiming that a runtime Dex was injected.
    """
    strategies: list[str] = []
    if plan.file_changes:
        strategies.append("existing_file")
    if plan.component_changes:
        strategies.append("manifest_component")
    if any(change.operation.value == "smali_insert_at_anchor" for change in plan.file_changes):
        strategies.append("minimal_smali_bridge")
    if len(strategies) > 1:
        strategies.append("hybrid")
    plan.detected_intents = list(dict.fromkeys(detected_intents))
    plan.patch_strategies = strategies
    return plan


def generate_plan(config, project_id, request, consent, *, analysis=None, model=None):
    with project_lock(config, project_id):
        require_clean_workspace(config, project_id)
        analysis = analysis or AnalysisService(config).analyze(
            project_id, ProjectWorkspace(project_id, config), persist=False
        )
        if not analysis:
            raise PlanServiceError("No analysis available")

        workspace = ProjectWorkspace(project_id, config)

        # Milestone 1: Pre-flight deterministic gates G1–G4 prior to model invocation or build
        from noir.application.gates import PreflightGateEngine

        gate_engine = PreflightGateEngine(config)
        g1_result = gate_engine.evaluate_g1(request, analysis=analysis, workspace=workspace)
        g2_result = gate_engine.evaluate_g2()
        g3_result = gate_engine.evaluate_g3(request, analysis=analysis, workspace=workspace)
        g4_result = gate_engine.evaluate_g4(workspace=workspace)

        if not g1_result.passed:
            raise PlanServiceError(f"Preflight gate G1 failed: {g1_result.message}")
        # Gate 2 blocks toolchain defects before corrupting APK
        if not g2_result.passed:
            raise PlanServiceError(f"Preflight gate G2 failed: {g2_result.message}")
        # Gate 3 blocks cross-layer regressions (e.g. DEX patch on Flutter/Unity AOT)
        if not g3_result.passed:
            raise PlanServiceError(f"Preflight gate G3 failed: {g3_result.message}")
        # Gate 4 blocks corrupted baseline workspaces
        if not g4_result.passed:
            raise PlanServiceError(f"Preflight gate G4 failed: {g4_result.message}")

        # Gate 1: Guaranteed operations are parsed and generated locally. Intent routing
        # is used only as a guard against silently dropping an advanced clause.
        from noir.application.deterministic_service import (
            create_deterministic_plan,
            parse_operation_spec,
        )
        from noir.infrastructure.ai.intent_router import IntentRouter

        deterministic_context = AiContextTools(workspace, analysis)
        routed = IntentRouter().route(request, deterministic_context, analysis)
        request_requirements = _request_requirements(request)
        spec = parse_operation_spec(request, set(routed.matched_intents))
        project = ProjectRepository().get(project_id)
        if spec and project and not request_requirements:
            plan = create_deterministic_plan(
                project_id, project.workspace_revision, request, spec, workspace
            )
            return PlanService(config).create_plan(plan)

        if not consent:
            raise PlanServiceError("Explicit AI upload consent required for advanced requests")

        # Check the plan cache before making any API calls.
        revision = project.workspace_revision if project else 0
        selected_model = model or config.ai_model
        cached = _plan_cache.get(project_id, revision, request, selected_model)
        if cached is not None:
            logger.info("Plan cache hit for project=%s revision=%d", project_id, revision)
            return cached

        context_tools = AiContextTools(workspace, analysis)
        generation_provider = _create_generation_provider(config, selected_model)
        budget = _WorkflowCallBudget(config)

        # Phase C: evidence-driven discovery before plan generation.
        discovery_transcript: list[dict] = []
        discovery_api_calls = 0
        discovery_stop_reason = ""
        detected_intents: list[str] = []
        try:
            from noir.infrastructure.ai.discovery import build_discovered_context
            from noir.infrastructure.ai.intent_router import IntentRouter

            route_result = IntentRouter().route(request, context_tools, analysis)
            detected_intents = list(route_result.matched_intents)
            if (
                route_result
                and route_result.matched_intents
                and route_result.seen_files
                and not request_requirements
            ):
                discovery_stop_reason = "intent_matched"
                discovery_api_calls = 0
                discovery_transcript = []
                context = build_discovered_context(
                    context_tools, route_result, user_request=request
                )
                context["detected_intents"] = route_result.matched_intents
            else:
                logger.warning(
                    "Intent routing was absent or incomplete; falling through to AI discovery"
                )
                discovery_provider = _create_discovery_provider(config)

                if discovery_provider is not None:
                    # OpenRouter or local provider — clean interface
                    discovery = discovery_provider.discover(request, context_tools, analysis)
                else:
                    # Legacy Gemini discovery path
                    from noir.infrastructure.ai.discovery import EvidenceDiscovery
                    from noir.infrastructure.ai.gemini import GeminiProvider

                    gemini_discovery = GeminiProvider(config=config, purpose="discovery")
                    discovery = EvidenceDiscovery(
                        gemini_discovery, context_tools, config, analysis
                    ).discover(request)

                # Preserve deterministic evidence for the clauses the router did
                # understand while discovery investigates the remaining clauses.
                if route_result:
                    for path, content in route_result.seen_files.items():
                        discovery.seen_files.setdefault(path, content)
                    for path, inspection in route_result.binary_inspections.items():
                        discovery.binary_inspections.setdefault(path, inspection)

                discovery_transcript = [r.to_dict() for r in discovery.transcript]
                discovery_api_calls = discovery.api_calls
                discovery_stop_reason = discovery.stop_reason
                budget.consume(discovery_api_calls, label="discovery")

                if not discovery.used_static_fallback:
                    context = build_discovered_context(
                        context_tools, discovery, user_request=request
                    )
                else:
                    context = context_tools.build_context(user_request=request)
        except PlanServiceError:
            raise  # budget exhaustion is a real error, not a fallback case
        except Exception:
            # Any discovery failure falls back to static selection (requirement #4).
            context = context_tools.build_context(user_request=request)

        context["request_requirements"] = list(request_requirements.values())

        budget.consume(1, label="plan generation")
        plan = generation_provider.generate_plan(request, analysis, context, project_id=project_id)
        plan.discovery_transcript = discovery_transcript
        plan.discovery_api_calls = discovery_api_calls
        plan.discovery_stop_reason = discovery_stop_reason
        _apply_strategy_metadata(plan, detected_intents)

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
        coverage_gaps = _plan_coverage_gaps(plan, detected_intents, request_requirements)
        if invalid or coverage_gaps:
            budget.consume(1, label="plan completeness correction")
            feedback: list[str] = []
            if invalid:
                feedback.append(_grounding_feedback(invalid, sorted(allowed_paths)))
            if coverage_gaps:
                feedback.append(_coverage_feedback(coverage_gaps))
            context["planning_feedback"] = "\n\n".join(feedback)
            plan = generation_provider.generate_plan(
                request, analysis, context, project_id=project_id
            )
            plan.discovery_transcript = discovery_transcript
            plan.discovery_api_calls = discovery_api_calls
            plan.discovery_stop_reason = discovery_stop_reason
            _apply_strategy_metadata(plan, detected_intents)
            invalid = _invalid_plan_paths(plan, workspace, allowed_paths)
            coverage_gaps = _plan_coverage_gaps(plan, detected_intents, request_requirements)
        if invalid:
            missing = ", ".join(invalid[:10])
            raise PlanServiceError(
                "AI proposed files that do not exist after one automatic grounded correction: "
                f"{missing}. No plan was saved."
            )
        if coverage_gaps:
            plan.unsupported_aspects.extend(
                f"NOIR could not prove complete implementation: {gap}" for gap in coverage_gaps
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
        if plan.unsupported_aspects:
            raise PlanServiceError(
                "This plan contains unsupported or unimplemented request clauses; revise the "
                "request before generating a patch"
            )
        if not preview and not ApprovalRepository().find_valid(
            project_id, ApprovalScope.PLAN, plan.compute_hash(), project.workspace_revision
        ):
            raise PlanServiceError("Approve the plan before any AI patch request")
        require_clean_workspace(config, project_id)
        if plan.execution_mode == "deterministic":
            from noir.application.deterministic_service import (
                create_deterministic_patch,
                operation_spec_from_plan,
            )

            try:
                spec = operation_spec_from_plan(plan)
            except ValueError as exc:
                raise PlanServiceError(str(exc)) from exc
            patch = create_deterministic_patch(
                ProjectWorkspace(project_id, config),
                plan,
                spec,
                project.original_sha256,
            )
            return PatchService(config).store_patch(patch, preview=preview)
        analysis = analysis or AnalysisService(config).analyze(
            project_id, ProjectWorkspace(project_id, config), persist=False
        )
        context = AiContextTools(ProjectWorkspace(project_id, config), analysis).build_context(
            [change.relative_path for change in plan.file_changes], user_request=plan.user_request
        )
        try:
            patch = _create_generation_provider(config, model).generate_patch(plan, context)
            patch.detected_intents = plan.detected_intents
            patch.patch_strategies = plan.patch_strategies
            patch.runtime_configuration = plan.runtime_configuration
            return PatchService(config).store_patch(patch, preview=preview)
        except Exception:
            # A retry must regenerate the plan instead of reusing the exact plan
            # whose patch proved invalid or incomplete.
            invalidate_plan_cache(project_id)
            raise
