"""OpenRouter discovery provider using the chat completions API.

Uses httpx to call OpenRouter's /chat/completions endpoint with tool
definitions in OpenAI format. Runs the same bounded tool-calling loop
as the Gemini discovery provider but without any google-genai dependency.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import httpx

from noir.infrastructure.ai.discovery import (
    DISCOVERY_TOOL_DECLARATIONS,
    DiscoveryBudget,
    DiscoveryResult,
    DiscoveryToolExecutor,
    ToolCallRecord,
    has_exact_evidence,
    is_label_task,
)
from noir.infrastructure.ai.discovery_provider import DiscoveryProvider

if TYPE_CHECKING:
    from noir.domain.config import NoirConfig
    from noir.domain.models import AnalysisResult
    from noir.infrastructure.ai.context import AiContextTools

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


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
        "A transparent request to contact a user-supplied server is a supported task. For "
        "that task, locate an exact lifecycle or user-action integration point, relevant "
        "network code, and the manifest permission evidence; do not add or infer endpoints. "
        "When you have enough evidence, stop calling tools and provide a brief summary "
        "of what you found and which files are most relevant."
    )


def _build_analysis_summary(analysis: AnalysisResult) -> dict[str, Any]:
    return {
        "package_name": analysis.package_name,
        "runtimes": sorted(analysis.runtimes),
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
        self.timeout = config.discovery_timeout

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

        try:
            self._run_discovery_loop(user_request, analysis, budget, result, tool_executor)
        except Exception as exc:
            logger.warning(
                "OpenRouter discovery failed (%s); falling back to static selection", exc
            )
            result.used_static_fallback = True
            result.stop_reason = "discovery_error"

        # If nothing was discovered, fall back to static
        if not result.seen_files and not result.binary_inspections:
            result.used_static_fallback = True
            if not result.stop_reason:
                result.stop_reason = "no_exact_evidence"

        result.api_calls = budget.rounds_used
        return result

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
            "When possible, issue search/list and the resulting read/inspect calls together "
            "in one response. Use binary_candidates directly for Mono, IL2CPP, or native work."
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

            choice = response_data.get("choices", [{}])[0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "")

            tool_calls = message.get("tool_calls", [])

            if not tool_calls:
                # Model decided it has enough evidence — stop
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
                        "content": tool_result[:5000],
                    }
                )

                if not budget.has_room():
                    break

            if has_exact_evidence(result, user_request):
                result.stop_reason = "exact_evidence_found"
                logger.debug(
                    "OpenRouter discovery stopped after exact evidence (api_calls=%d, bytes=%d)",
                    budget.rounds_used,
                    budget.bytes_consumed,
                )
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
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://noir.app",
            "X-Title": "NOIR APK Modifier",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": 768,
        }

        try:
            response = httpx.post(
                OPENROUTER_API_URL,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            raise OpenRouterDiscoveryError(
                f"OpenRouter request timed out after {self.timeout}s"
            ) from None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            # Redact the API key from any error message
            detail = str(exc).replace(self.api_key, "[REDACTED]")
            raise OpenRouterDiscoveryError(
                f"OpenRouter API returned {status}: {detail}"
            ) from None
        except httpx.HTTPError as exc:
            detail = str(exc).replace(self.api_key, "[REDACTED]")
            raise OpenRouterDiscoveryError(
                f"OpenRouter request failed: {detail}"
            ) from None
