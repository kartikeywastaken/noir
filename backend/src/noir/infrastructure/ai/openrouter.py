"""OpenRouter discovery provider using the chat completions API.

Uses httpx to call OpenRouter's /chat/completions endpoint with tool
definitions in OpenAI format. Runs the same bounded tool-calling loop
as the Gemini discovery provider but without any google-genai dependency.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any

import httpx

from noir.infrastructure.ai.discovery import (
    DISCOVERY_TOOL_DECLARATIONS,
    DiscoveryBudget,
    DiscoveryResult,
    DiscoveryToolExecutor,
    ToolCallRecord,
    is_label_task,
)
from noir.infrastructure.ai.discovery_provider import DiscoveryProvider

if TYPE_CHECKING:
    from noir.domain.config import NoirConfig
    from noir.domain.models import AnalysisResult
    from noir.infrastructure.ai.context import AiContextTools

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
_UI_REQUEST = re.compile(
    r"\b(ui|screen|button|message|toast|flash|interact(?:ion)?|click|tap|touch|keypress)\b",
    re.IGNORECASE,
)
_RESOURCE_TEXT_REQUEST = re.compile(
    r"\b(label|title|text|string|wording|menu|button|settings|display[ -]?name)\b",
    re.IGNORECASE,
)


class OpenRouterDiscoveryError(Exception):
    """Raised when OpenRouter API operations fail."""

    pass


def _openai_tool_definitions() -> list[dict[str, Any]]:
    """Convert our tool declarations to OpenAI-format tool definitions."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        }
        for tool in DISCOVERY_TOOL_DECLARATIONS
    ]


def _build_system_message() -> str:
    return (
        "You are an Android APK workspace investigator. Your job is to find the exact "
        "files and code relevant to the user's modification request. Use the provided "
        "tools to search, list, and read files. Focus on finding evidence: specific files, "
        "classes, methods, or values that are directly relevant to the request. "
        "Search results are leads, not evidence: read the relevant result before finishing. "
        "For UI interaction requests, identify the app's actual UI technology and launcher "
        "components, then search for concrete callbacks such as onClick/onTouch/onKey, "
        "listener registration, or Compose click lambdas. Do not declare Smali unavailable "
        "until you have searched every decoded smali* tree for those integration points. "
        "For React Native, use runtime_evidence to distinguish a plain bundle from confirmed "
        "Hermes bytecode. Never treat a bundle listed under hermes_bytecode as UTF-8 JavaScript "
        "or claim source-level edits are available; locate a supported Android-side integration "
        "point or report that exact limitation. "
        "File reads default to 100,000 characters and can return up to 300,000; request a "
        "later start offset when the target lies outside the first excerpt. "
        "A transparent request to contact a user-supplied server is a supported task. For "
        "that task, locate an exact lifecycle or user-action integration point, relevant "
        "network code, and the manifest permission evidence; do not add or infer endpoints. "
        "When you have enough evidence, stop calling tools and provide a brief summary "
        "of what you found and which files are most relevant."
    )


def _build_analysis_summary(analysis: AnalysisResult) -> dict[str, Any]:
    smali_by_descriptor = {entry.descriptor: entry for entry in analysis.smali_classes}

    def described_component(component) -> dict[str, Any]:
        name = component.name
        if name.startswith("."):
            qualified = f"{analysis.package_name}{name}"
        elif "." not in name:
            qualified = f"{analysis.package_name}.{name}"
        else:
            qualified = name
        descriptor = f"L{qualified.replace('.', '/')};"
        smali = smali_by_descriptor.get(descriptor)
        methods: list[str] = []
        if smali:
            callbacks = [
                method
                for method in smali.methods
                if method.startswith("on") or "Click" in method or "Touch" in method
            ]
            methods = list(dict.fromkeys([*callbacks, *smali.methods]))[:30]
        return {
            "name": name,
            "component_type": component.component_type,
            "is_launcher": component.is_launcher,
            "smali_file": smali.file_path if smali else None,
            "methods": methods,
        }

    components = sorted(
        analysis.components,
        key=lambda item: (
            not item.is_launcher,
            item.component_type != "activity",
            item.name,
        ),
    )
    return {
        "package_name": analysis.package_name,
        "runtimes": sorted(analysis.runtimes),
        "runtime_evidence": analysis.runtime_evidence,
        "application_class": analysis.application_class,
        "manifest_components": [described_component(item) for item in components[:20]],
        "smali_class_count": len(analysis.smali_classes),
        "native_abis": analysis.native_abis,
        "managed_assemblies": analysis.managed_assemblies[:10],
        "assets_sample": analysis.assets[:20],
        "binary_candidates": [
            *analysis.managed_assemblies[:10],
            *[
                f"lib/{entry.abi}/{library}"
                for entry in analysis.native_libs
                for library in entry.libraries
            ][:20],
        ],
    }


class OpenRouterDiscoveryProvider(DiscoveryProvider):
    """Discovery provider using OpenRouter's chat completions API.

    Sends tool definitions in OpenAI format and processes tool_calls from
    the response. Uses httpx for HTTP requests with bounded timeouts.
    """

    def __init__(self, config: NoirConfig):
        super().__init__(config)
        self.api_key = config.openrouter_api_key.get_secret_value()
        self.model = config.openrouter_discovery_model
        self.fallback_model = config.openrouter_discovery_fallback_model
        self.timeout = config.discovery_timeout
        self._client: httpx.Client | None = None

        if not self.api_key:
            raise OpenRouterDiscoveryError(
                "OpenRouter API key not configured. Set NOIR_OPENROUTER_API_KEY in your "
                "environment, then restart the process."
            )

    def discover(
        self,
        user_request: str,
        context_tools: AiContextTools,
        analysis: AnalysisResult,
    ) -> DiscoveryResult:
        """Run bounded evidence discovery via OpenRouter."""
        # Kill switch
        if not self.config.discovery_enabled:
            logger.debug("Discovery disabled by config; using static selection")
            return DiscoveryResult(used_static_fallback=True, stop_reason="discovery_disabled")

        # Label tasks skip discovery
        if is_label_task(user_request):
            logger.debug("Label task detected; skipping discovery")
            return DiscoveryResult(used_static_fallback=True, stop_reason="static_fast_path")

        budget = DiscoveryBudget(
            max_rounds=self.config.discovery_max_rounds,
            max_total_bytes=self.config.ai_max_request_size,
        )
        result = DiscoveryResult()
        tool_executor = DiscoveryToolExecutor(context_tools)
        self._seed_default_string_resources(
            user_request, budget, result, tool_executor, context_tools
        )
        self._seed_launcher_evidence(user_request, analysis, budget, result, tool_executor)

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://noir.app",
                "X-Title": "NOIR APK Modifier",
            }
            with httpx.Client(headers=headers, timeout=self.timeout) as client:
                self._client = client
                self._run_discovery_loop(user_request, analysis, budget, result, tool_executor)
        except Exception as exc:
            logger.warning(
                "OpenRouter discovery failed (%s); falling back to static selection", exc
            )
            result.used_static_fallback = True
            result.stop_reason = "discovery_error"
        finally:
            self._client = None

        # If nothing was discovered, fall back to static
        if not result.seen_files and not result.binary_inspections:
            result.used_static_fallback = True
            if not result.stop_reason:
                result.stop_reason = "no_exact_evidence"

        result.api_calls = budget.rounds_used
        return result

    @staticmethod
    def _seed_default_string_resources(
        user_request: str,
        budget: DiscoveryBudget,
        result: DiscoveryResult,
        tool_executor: DiscoveryToolExecutor,
        context_tools: AiContextTools,
    ) -> None:
        """Preload default Android strings for text-oriented requests.

        This is local evidence collection, so it costs no provider request. It
        prevents a two-round discovery budget from being spent on
        ``list res/values`` followed by ``list res`` without ever reading the
        obvious default string table.
        """
        if not _RESOURCE_TEXT_REQUEST.search(user_request):
            return
        path = "res/values/strings.xml"
        try:
            size = context_tools.workspace.safe_path(path).stat().st_size
        except (FileNotFoundError, OSError, ValueError):
            return
        max_chars = min(size, 100_000)
        if max_chars <= 0 or budget.bytes_consumed + max_chars > budget.max_total_bytes:
            return
        arguments = {"path": path, "start": 0, "max_chars": max_chars}
        content, tool_summary, nbytes = tool_executor.execute(
            "read_file_excerpt", arguments, result
        )
        if content.startswith("Error:") or not nbytes:
            return
        budget.consume_bytes(nbytes)
        result.transcript.append(
            ToolCallRecord(
                tool_name="read_file_excerpt",
                arguments={**arguments, "source": "default_strings_seed"},
                bytes_returned=nbytes,
                result_summary=f"Host-seeded Android string evidence: {tool_summary}",
            )
        )

    @staticmethod
    def _seed_launcher_evidence(
        user_request: str,
        analysis: AnalysisResult,
        budget: DiscoveryBudget,
        result: DiscoveryResult,
        tool_executor: DiscoveryToolExecutor,
    ) -> None:
        """Preload the exact launcher Smali for requests that clearly target UI behavior."""
        if not _UI_REQUEST.search(user_request):
            return
        summary = _build_analysis_summary(analysis)
        components = summary.get("manifest_components", [])
        launcher = next(
            (
                item
                for item in components
                if item.get("is_launcher") and item.get("smali_file")
            ),
            None,
        )
        if launcher is None:
            return
        arguments = {"path": launcher["smali_file"], "start": 0, "max_chars": 100_000}
        content, tool_summary, nbytes = tool_executor.execute(
            "read_file_excerpt", arguments, result
        )
        if content.startswith("Error:") or not nbytes:
            return
        budget.consume_bytes(nbytes)
        result.transcript.append(
            ToolCallRecord(
                tool_name="read_file_excerpt",
                arguments={**arguments, "source": "manifest_launcher_seed"},
                bytes_returned=nbytes,
                result_summary=f"Host-seeded launcher evidence: {tool_summary}",
            )
        )

    def _run_discovery_loop(
        self,
        user_request: str,
        analysis: AnalysisResult,
        budget: DiscoveryBudget,
        result: DiscoveryResult,
        tool_executor: DiscoveryToolExecutor,
    ) -> None:
        """Multi-turn tool-calling loop via OpenRouter."""
        summary = _build_analysis_summary(analysis)
        initial_content = (
            f"USER REQUEST: {user_request}\n\n"
            f"APP ANALYSIS SUMMARY:\n{json.dumps(summary, indent=2)}\n\n"
            "Find the smallest set of exact files or binaries relevant to this request. "
            "The manifest_components entries already map Android components to exact Smali "
            "paths when the analyzer found them; read those paths directly instead of searching "
            "for text the user wants to add. Never search for a new requested literal as though "
            "it should already exist. "
            "When possible, issue search/list and the resulting read/inspect calls together "
            "in one response. Use binary_candidates directly for Mono, IL2CPP, or native work. "
            "A path under runtime_evidence.hermes_bytecode is compiled Hermes bytecode, not "
            "editable JavaScript."
        )
        if result.seen_files:
            seeded = "\n\n".join(
                f"FILE: {path}\n{content}" for path, content in result.seen_files.items()
            )
            initial_content += (
                "\n\nHOST-PRELOADED EXACT EVIDENCE (already read; do not request it again):\n"
                f"{seeded}"
            )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _build_system_message()},
            {"role": "user", "content": initial_content},
        ]

        tools = _openai_tool_definitions()
        tool_cache: dict[str, tuple[str, str, int]] = {}

        while budget.has_room():
            budget.begin_round()

            response_data = self._call_api(messages, tools)

            choices = response_data.get("choices")
            if not isinstance(choices, list) or not choices:
                raise OpenRouterDiscoveryError("OpenRouter returned no response choices")
            choice = choices[0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "")

            tool_calls = message.get("tool_calls", [])

            if not tool_calls:
                if finish_reason != "stop":
                    raise OpenRouterDiscoveryError(
                        "OpenRouter stopped without a complete response "
                        f"({finish_reason or 'unknown'})"
                    )
                result.stop_reason = "model_finished"
                break

            # Append the assistant message (with tool_calls) to conversation
            messages.append(message)

            # Process each tool call
            for tc in tool_calls:
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}

                cache_key = json.dumps(
                    [tool_name, args], sort_keys=True, separators=(",", ":"), default=str
                )
                cached = tool_cache.get(cache_key)
                if cached is None:
                    tool_result, tool_summary, nbytes = tool_executor.execute(
                        tool_name, args, result
                    )
                    tool_cache[cache_key] = (tool_result, tool_summary, nbytes)
                    charged_bytes = nbytes
                else:
                    tool_result, tool_summary, _ = cached
                    tool_summary = f"Cached: {tool_summary}"
                    charged_bytes = 0

                record = ToolCallRecord(
                    tool_name=tool_name,
                    arguments=args,
                    bytes_returned=charged_bytes,
                    result_summary=tool_summary,
                )
                result.transcript.append(record)
                budget.consume_bytes(charged_bytes)

                # Append the tool response to the conversation
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": tool_result,
                    }
                )

                if not budget.has_room():
                    break

            if not budget.has_room():
                logger.debug(
                    "OpenRouter discovery budget exhausted (rounds=%d, bytes=%d)",
                    budget.rounds_used,
                    budget.bytes_consumed,
                )
                result.stop_reason = "budget_exhausted"
                break

        if not result.stop_reason and not budget.has_room():
            result.stop_reason = "budget_exhausted"

    def _call_api(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Make a single call to the OpenRouter API."""
        payload: dict[str, Any] = {
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": 768,
        }
        client = self._client
        if client is None:
            raise OpenRouterDiscoveryError("OpenRouter HTTP client is not active")

        models = list(dict.fromkeys(filter(None, [self.model, self.fallback_model])))
        last_error = "OpenRouter request failed"
        for model_index, model in enumerate(models):
            payload["model"] = model
            for attempt in range(self.config.ai_retry_limit + 1):
                response: httpx.Response | None = None
                try:
                    response = client.post(OPENROUTER_API_URL, json=payload)
                except httpx.TimeoutException:
                    last_error = f"OpenRouter model {model} timed out after {self.timeout}s"
                    retryable = True
                except httpx.HTTPError as exc:
                    detail = str(exc).replace(self.api_key, "[REDACTED]")
                    last_error = f"OpenRouter request failed for {model}: {detail}"
                    retryable = True
                else:
                    if response.status_code < 400:
                        try:
                            data = response.json()
                        except ValueError:
                            raise OpenRouterDiscoveryError(
                                "OpenRouter returned malformed JSON"
                            ) from None
                        if not isinstance(data, dict):
                            raise OpenRouterDiscoveryError("OpenRouter returned invalid JSON")
                        return data
                    retryable = response.status_code == 429 or response.status_code >= 500
                    last_error = f"OpenRouter API returned {response.status_code} for {model}"
                    if not retryable:
                        raise OpenRouterDiscoveryError(last_error)

                if retryable and attempt < self.config.ai_retry_limit:
                    retry_after = 1.0
                    if response is not None:
                        try:
                            retry_after = float(response.headers.get("Retry-After", "1"))
                        except ValueError:
                            retry_after = 1.0
                    time.sleep(min(max(retry_after, 0.25), 5.0))
                    continue
                break

            if model_index + 1 < len(models):
                logger.warning("%s; trying discovery fallback model", last_error)

        raise OpenRouterDiscoveryError(last_error)
