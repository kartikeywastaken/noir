"""Google ADK-backed AI providers for NOIR.

ADK owns model orchestration and bounded workspace-tool use.  NOIR still owns
authorization, approval, schema validation, patch application, rebuilding,
signing, and auditing.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from noir.infrastructure.ai.discovery import (
    DiscoveryBudget,
    DiscoveryResult,
    DiscoveryToolExecutor,
    ToolCallRecord,
    is_label_task,
)
from noir.infrastructure.ai.discovery_provider import DiscoveryProvider
from noir.infrastructure.ai.gemini import (
    GeminiProvider,
    GeminiProviderError,
    _is_retryable_availability_error,
    _requires_textual_schema,
    _schema_prompt_suffix,
    _schema_size,
)
from noir.infrastructure.ai.openrouter import _build_analysis_summary, _build_system_message

if TYPE_CHECKING:
    from noir.domain.config import NoirConfig
    from noir.domain.models import AnalysisResult
    from noir.infrastructure.ai.context import AiContextTools

logger = logging.getLogger(__name__)


def _final_text(events: list[Any]) -> str:
    """Return the final textual ADK response and reject missing output."""
    final = ""
    provider_error = ""
    for event in events:
        output = getattr(event, "output", None)
        if isinstance(output, dict) and "result" in output:
            result = output["result"]
            final = result if isinstance(result, str) else json.dumps(result, separators=(",", ":"))
        elif isinstance(output, dict):
            final = json.dumps(output, separators=(",", ":"))
        elif isinstance(output, str):
            final = output
        else:
            model_dump_json = getattr(output, "model_dump_json", None)
            if callable(model_dump_json):
                final = model_dump_json()
        if not event.is_final_response():
            continue
        if event.error_code or event.error_message:
            provider_error = ": ".join(
                str(value) for value in (event.error_code, event.error_message) if value
            )
        if not event.content:
            continue
        parts = event.content.parts or []
        text_parts = [part.text for part in parts if isinstance(part.text, str)]
        if text_parts:
            final = "".join(text_parts)
    if not final.strip():
        if provider_error:
            raise GeminiProviderError(f"Google ADK model error: {provider_error}")
        raise GeminiProviderError("Google ADK returned no completed textual response")
    return final


class AdkGeminiProvider(GeminiProvider):
    """Generate NOIR plans and patches through a single-turn ADK agent.

    The existing GeminiProvider owns prompt construction and conversion into
    NOIR domain models.  This subclass swaps only the model execution layer, so
    the plan/patch schemas and all host-side checks remain identical.
    """

    provider_name = "google-adk"

    def _run_agent(
        self,
        *,
        model_name: str,
        prompt: str,
        system_instruction: str,
        response_schema: dict[str, Any] | None,
        json_output: bool,
        token_budget: int,
    ) -> str:
        from google import genai
        from google.adk.agents import LlmAgent
        from google.adk.agents.run_config import RunConfig
        from google.adk.models import Gemini
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types

        client = genai.Client(
            api_key=self.api_key,
            http_options=types.HttpOptions(
                timeout=self.timeout * 1000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        model = Gemini(model=model_name, client=client)
        agent = LlmAgent(
            name=f"noir_{self.purpose}_agent",
            description="Produces an exact NOIR APK modification artifact.",
            model=model,
            instruction=system_instruction,
            output_schema=response_schema,
            generate_content_config=types.GenerateContentConfig(
                max_output_tokens=token_budget,
                response_mime_type=("application/json" if json_output else "text/plain"),
            ),
            mode="task",
            timeout=float(self.config.ai_stall_timeout),
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
        )
        runner = Runner(
            agent=agent,
            app_name="noir_adk",
            session_service=InMemorySessionService(),
            auto_create_session=True,
        )
        message = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        events = list(
            runner.run(
                user_id="noir",
                session_id=uuid.uuid4().hex,
                new_message=message,
                run_config=RunConfig(max_llm_calls=1),
            )
        )
        return _final_text(events)

    def _call_model(
        self,
        prompt: str,
        system_instruction: str = "",
        *,
        json_output: bool = True,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        """Run one bounded ADK agent turn with the existing fallback policy."""
        actual = (
            len(prompt.encode("utf-8"))
            + len(system_instruction.encode("utf-8"))
            + _schema_size(response_schema)
        )
        if actual > self.config.ai_max_request_size:
            raise GeminiProviderError(
                f"AI request requires {actual:,} bytes; configured limit is "
                f"{self.config.ai_max_request_size:,}. No request was sent."
            )

        attempts = 1 + self.config.ai_response_retry_limit if json_output else 1
        token_budget = self.max_output_tokens
        stall_deadline = time.monotonic() + self.config.ai_stall_timeout
        models = [self.model_name]
        for fb in self.fallback_model_names:
            if fb and fb != self.model_name and fb not in models:
                models.append(fb)

        for attempt in range(1, attempts + 1):
            failure = ""
            for model_index, model_name in enumerate(models):
                if time.monotonic() > stall_deadline:
                    raise GeminiProviderError(
                        f"AI call exceeded stall timeout ({self.config.ai_stall_timeout}s). "
                        "No response was used."
                    )

                textual_schema = response_schema is not None and _requires_textual_schema(
                    model_name
                )
                active_schema = None if textual_schema else response_schema
                active_prompt = (
                    prompt + _schema_prompt_suffix(response_schema) if textual_schema else prompt
                )
                try:
                    text = self._run_agent(
                        model_name=model_name,
                        prompt=active_prompt,
                        system_instruction=system_instruction,
                        response_schema=active_schema,
                        json_output=json_output,
                        token_budget=token_budget,
                    )
                    self.last_model_name = model_name
                except Exception as exc:
                    can_fallback = model_index == 0 and len(models) > 1
                    if can_fallback and _is_retryable_availability_error(exc, has_fallback=True):
                        logger.warning(
                            "ADK model %s is unavailable; retrying with %s",
                            model_name,
                            self.fallback_model_name,
                        )
                        continue
                    detail_upper = str(exc).upper()
                    is_quota = (
                        getattr(exc, "status_code", None) == 429
                        or "429" in detail_upper
                        or "RESOURCE_EXHAUSTED" in detail_upper
                    )
                    if is_quota:
                        raise GeminiProviderError(
                            "Gemini daily quota exhausted. Wait for quota reset or configure "
                            "NOIR_AI_FALLBACK_MODEL."
                        ) from None
                    detail = str(exc).replace(self.api_key, "[REDACTED]")
                    raise GeminiProviderError(f"Google ADK call failed: {detail}") from None

                if len(text.encode()) > self.config.ai_max_output_size:
                    failure = "Google ADK returned an oversized response"
                elif json_output:
                    try:
                        self._parse_json_response(text)
                    except GeminiProviderError as exc:
                        failure = str(exc)
                    else:
                        return text
                else:
                    return text
                break

            if not failure:
                raise GeminiProviderError("Google ADK returned no usable response")
            if attempt == attempts:
                raise GeminiProviderError(
                    f"{failure} after {attempts} ADK generation attempt(s). No partial "
                    "response was used or applied."
                )
            token_budget = min(token_budget * 2, 65_536)
            logger.warning(
                "Incomplete or invalid ADK JSON; regenerating from the original instructions "
                "(attempt %s/%s)",
                attempt + 1,
                attempts,
            )

        raise GeminiProviderError("Google ADK returned no complete response")


class AdkDiscoveryProvider(DiscoveryProvider):
    """Use an ADK tool-using agent for bounded APK evidence discovery."""

    def __init__(self, config: NoirConfig):
        super().__init__(config)
        self.api_key = config.gemini_key_for("discovery")
        self.model_name = config.ai_model
        if not self.api_key:
            raise GeminiProviderError(
                "Gemini discovery key not configured. Set GEMINI_API_KEY_1 or GEMINI_API_KEY."
            )

    def discover(
        self,
        user_request: str,
        context_tools: AiContextTools,
        analysis: AnalysisResult,
    ) -> DiscoveryResult:
        if not self.config.discovery_enabled:
            return DiscoveryResult(used_static_fallback=True, stop_reason="discovery_disabled")
        if is_label_task(user_request):
            return DiscoveryResult(used_static_fallback=True, stop_reason="static_fast_path")

        budget = DiscoveryBudget(
            max_rounds=self.config.discovery_max_rounds,
            max_total_bytes=self.config.ai_max_request_size,
        )
        result = DiscoveryResult()
        executor = DiscoveryToolExecutor(context_tools)
        cache: dict[str, tuple[str, str, int]] = {}

        def invoke(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
            cache_key = json.dumps([tool_name, args], sort_keys=True, default=str)
            cached = cache.get(cache_key)
            if cached is not None:
                text, summary, _ = cached
                result.transcript.append(
                    ToolCallRecord(
                        tool_name=tool_name,
                        arguments=args,
                        result_summary=f"Cached: {summary}",
                    )
                )
                return {"result": text, "summary": f"Cached: {summary}"}

            remaining = budget.max_total_bytes - budget.bytes_consumed
            if remaining <= 0:
                return {"error": "Discovery evidence byte budget exhausted"}
            bounded_args = dict(args)
            if tool_name == "read_file_excerpt":
                requested = int(bounded_args.get("max_chars", 100_000))
                bounded_args["max_chars"] = max(1, min(requested, remaining))

            seen_before = dict(result.seen_files)
            binary_before = dict(result.binary_inspections)
            text, summary, nbytes = executor.execute(tool_name, bounded_args, result)
            if nbytes > remaining:
                result.seen_files = seen_before
                result.binary_inspections = binary_before
                return {"error": "Tool result exceeds remaining discovery byte budget"}

            budget.consume_bytes(nbytes)
            result.transcript.append(
                ToolCallRecord(
                    tool_name=tool_name,
                    arguments=bounded_args,
                    bytes_returned=nbytes,
                    result_summary=summary,
                )
            )
            cache[cache_key] = (text, summary, nbytes)
            return {"result": text, "summary": summary}

        def search_workspace(query: str, glob: str = "*") -> dict[str, Any]:
            """Search decoded APK text and return exact matching file locations."""
            return invoke("search_workspace", {"query": query, "glob": glob})

        def list_directory(subdir: str) -> dict[str, Any]:
            """List decoded workspace paths beneath a relative directory."""
            return invoke("list_directory", {"subdir": subdir})

        def read_file_excerpt(
            path: str, start: int = 0, max_chars: int = 100_000
        ) -> dict[str, Any]:
            """Read a bounded exact text excerpt from one decoded workspace file."""
            return invoke(
                "read_file_excerpt",
                {"path": path, "start": start, "max_chars": max_chars},
            )

        def inspect_binary(path: str) -> dict[str, Any]:
            """Inspect one Mono, IL2CPP, or ELF binary using NOIR's bounded host tools."""
            return invoke("inspect_binary", {"path": path})

        try:
            self._run_discovery_agent(
                user_request=user_request,
                analysis=analysis,
                tools=[search_workspace, list_directory, read_file_excerpt, inspect_binary],
                budget=budget,
                result=result,
            )
        except Exception as exc:
            if result.seen_files or result.binary_inspections:
                logger.warning("ADK discovery stopped after collecting evidence: %s", exc)
                result.stop_reason = "agent_limit_with_evidence"
            else:
                logger.warning("ADK discovery failed; using static context: %s", exc)
                result.used_static_fallback = True
                result.stop_reason = "discovery_error"

        if not result.seen_files and not result.binary_inspections:
            result.used_static_fallback = True
            if not result.stop_reason:
                result.stop_reason = "no_exact_evidence"
        result.api_calls = budget.rounds_used
        return result

    def _run_discovery_agent(
        self,
        *,
        user_request: str,
        analysis: AnalysisResult,
        tools: list[Any],
        budget: DiscoveryBudget,
        result: DiscoveryResult,
    ) -> None:
        from google import genai
        from google.adk.agents import LlmAgent
        from google.adk.agents.run_config import RunConfig
        from google.adk.models import Gemini
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types

        client = genai.Client(
            api_key=self.api_key,
            http_options=types.HttpOptions(
                timeout=self.config.discovery_timeout * 1000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

        def count_model_call(callback_context: Any, llm_request: Any) -> None:
            del callback_context, llm_request
            budget.begin_round()
            return None

        agent = LlmAgent(
            name="noir_discovery_agent",
            description="Finds exact evidence in a decoded Android APK workspace.",
            model=Gemini(model=self.model_name, client=client),
            instruction=_build_system_message(),
            tools=tools,
            generate_content_config=types.GenerateContentConfig(max_output_tokens=768),
            mode="task",
            timeout=float(self.config.ai_stall_timeout),
            before_model_callback=count_model_call,
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
        )
        summary = _build_analysis_summary(analysis)
        prompt = (
            f"USER REQUEST: {user_request}\n\n"
            f"APP ANALYSIS SUMMARY:\n{json.dumps(summary, indent=2)}\n\n"
            "Use the tools to find the smallest exact set of files or binaries needed to "
            "implement this request. Search results are leads: read or inspect the target "
            "before finishing. Do not propose or apply a patch in this discovery stage."
        )
        runner = Runner(
            agent=agent,
            app_name="noir_adk_discovery",
            session_service=InMemorySessionService(),
            auto_create_session=True,
        )
        message = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        events = list(
            runner.run(
                user_id="noir",
                session_id=uuid.uuid4().hex,
                new_message=message,
                run_config=RunConfig(max_llm_calls=self.config.discovery_max_rounds),
            )
        )
        result.stop_reason = "model_finished" if _final_text(events) else "empty_model_response"
