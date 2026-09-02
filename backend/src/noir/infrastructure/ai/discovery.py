"""Bounded evidence-discovery phase for AI plan generation.

Runs a tool-using conversation with Gemini BEFORE the final planning call,
allowing the model to search, list, and read workspace files to gather the
exact evidence it needs — rather than receiving a fixed pre-selected bundle.

Discovery is bounded by both round count and cumulative byte budget. If the
model doesn't request anything, or the budget runs out, the system falls back
to today's static heuristic selection.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from noir.domain.config import NoirConfig
    from noir.domain.models import AnalysisResult
    from noir.infrastructure.ai.context import AiContextTools

logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────────


@dataclass
class ToolCallRecord:
    """One tool call and its result, for audit trail."""

    tool_name: str
    arguments: dict[str, Any]
    bytes_returned: int = 0
    result_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "bytes_returned": self.bytes_returned,
            "result_summary": self.result_summary,
        }


@dataclass
class DiscoveryBudget:
    """Tracks model requests and returned evidence against configured limits."""

    max_rounds: int = 2
    max_total_bytes: int = 100_000
    rounds_used: int = 0
    bytes_consumed: int = 0

    def has_room(self, proposed_bytes: int = 0) -> bool:
        return (
            self.rounds_used < self.max_rounds
            and self.bytes_consumed + proposed_bytes <= self.max_total_bytes
        )

    def begin_round(self) -> None:
        """Record one remote model request, independent of its tool-call count."""
        self.rounds_used += 1

    def consume_bytes(self, nbytes: int) -> None:
        self.bytes_consumed += nbytes


@dataclass
class DiscoveryResult:
    """Output of the discovery phase — same shape consumers expect."""

    seen_files: dict[str, str] = field(default_factory=dict)
    binary_inspections: dict[str, Any] = field(default_factory=dict)
    transcript: list[ToolCallRecord] = field(default_factory=list)
    used_static_fallback: bool = False
    api_calls: int = 0
    stop_reason: str = ""


# ── Tool definitions ─────────────────────────────────────────────────

_DISCOVERY_TOOL_DECLARATIONS = [
    {
        "name": "search_workspace",
        "description": (
            "Search decoded file contents for a text pattern. Use this to find WHERE a "
            "concept (a currency value, a check, a string) actually lives, when you "
            "don't know the exact file path yet."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to search for"},
                "glob": {
                    "type": "string",
                    "description": "Optional glob filter, e.g. '*.smali'",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_directory",
        "description": "List files under a workspace subdirectory.",
        "parameters": {
            "type": "object",
            "properties": {
                "subdir": {
                    "type": "string",
                    "description": "Subdirectory to list, e.g. 'res/values'",
                },
            },
            "required": ["subdir"],
        },
    },
    {
        "name": "read_file_excerpt",
        "description": (
            "Read up to 50KB of a specific file you've identified as relevant. "
            "Use this after search_workspace or list_directory has pointed you at a "
            "specific path."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_binary",
        "description": (
            "For Mono/IL2CPP/native targets: inspect a specific .dll or .so "
            "file's types, methods, symbols and disassembly."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to .dll or .so"},
            },
            "required": ["path"],
        },
    },
]


def _gemini_tool_declarations():
    """Build google.genai tool declarations from our tool list."""
    from google.genai import types

    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=tool["name"],
                    description=tool["description"],
                    parameters=tool["parameters"],
                )
                for tool in _DISCOVERY_TOOL_DECLARATIONS
            ]
        )
    ]


# ── Discovery engine ─────────────────────────────────────────────────


class EvidenceDiscovery:
    """Bounded tool-using discovery loop.

    Runs immediately before plan generation. Returns a DiscoveryResult
    that can be used to build the same context shape that build_context()
    produces, so generate_plan() downstream doesn't need to change.
    """

    def __init__(
        self,
        provider,  # GeminiProvider — we use its _get_client() and model config
        context_tools: AiContextTools,
        config: NoirConfig,
        analysis: AnalysisResult,
    ):
        self.provider = provider
        self.context_tools = context_tools
        self.config = config
        self.analysis = analysis

    def _is_label_task(self, user_request: str) -> bool:
        return bool(
            re.search(
                r"\b(label|rename|app[ -]?name|display[ -]?name)\b|name of (?:the )?app",
                user_request,
                re.IGNORECASE,
            )
        )

    def discover(self, user_request: str) -> DiscoveryResult:
        """Run the bounded discovery loop."""
        # Kill switch
        if not self.config.discovery_enabled:
            logger.debug("Discovery disabled by config; using static selection")
            return DiscoveryResult(used_static_fallback=True, stop_reason="discovery_disabled")

        # Label tasks are already well-served by static selection
        if self._is_label_task(user_request):
            logger.debug("Label task detected; skipping discovery")
            return DiscoveryResult(used_static_fallback=True, stop_reason="static_fast_path")

        budget = DiscoveryBudget(
            max_rounds=self.config.discovery_max_rounds,
            max_total_bytes=self.config.ai_max_request_size,
        )
        result = DiscoveryResult()

        try:
            self._run_discovery_loop(user_request, budget, result)
        except Exception as exc:
            logger.warning("Discovery phase failed (%s); falling back to static selection", exc)
            result.used_static_fallback = True
            result.stop_reason = "discovery_error"

        # Requirement #4: if nothing was discovered, fall back to static
        if not result.seen_files and not result.binary_inspections:
            result.used_static_fallback = True
            if not result.stop_reason:
                result.stop_reason = "no_exact_evidence"

        result.api_calls = budget.rounds_used

        return result

    @staticmethod
    def _has_exact_evidence(result: DiscoveryResult, user_request: str) -> bool:
        """Return whether discovery has host-grounded content ready for planning.

        Search/list results are only leads. A successful file read or structured
        binary inspection is exact evidence and avoids paying for another model
        turn whose only purpose would be to summarize data already held locally.
        AndroidManifest.xml alone is sufficient only for manifest-oriented work.
        """
        if result.binary_inspections:
            return True
        non_manifest_reads = {path for path in result.seen_files if path != "AndroidManifest.xml"}
        if non_manifest_reads:
            return True
        return bool(
            result.seen_files
            and re.search(
                r"\b(manifest|permission|activity|service|receiver|provider|intent)\b",
                user_request,
                re.IGNORECASE,
            )
        )

    def _run_discovery_loop(
        self,
        user_request: str,
        budget: DiscoveryBudget,
        result: DiscoveryResult,
    ) -> None:
        """Multi-turn function-calling loop with Gemini."""
        from google.genai import types

        client = self.provider._get_client()
        tools = _gemini_tool_declarations()

        system_instruction = (
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

        analysis_summary = {
            "package_name": self.analysis.package_name,
            "runtimes": sorted(self.analysis.runtimes),
            "smali_class_count": len(self.analysis.smali_classes),
            "native_abis": self.analysis.native_abis,
            "managed_assemblies": self.analysis.managed_assemblies[:10],
            "assets_sample": self.analysis.assets[:20],
            "binary_candidates": [
                *self.analysis.managed_assemblies[:10],
                *[
                    f"lib/{entry.abi}/{library}"
                    for entry in self.analysis.native_libs
                    for library in entry.libraries
                ][:20],
            ],
        }

        initial_prompt = (
            f"USER REQUEST: {user_request}\n\n"
            f"APP ANALYSIS SUMMARY:\n{json.dumps(analysis_summary, indent=2)}\n\n"
            "Find the smallest set of exact files or binaries relevant to this request. "
            "When possible, issue search/list and the resulting read/inspect calls together "
            "in one response. Use binary_candidates directly for Mono, IL2CPP, or native work."
        )

        contents = [types.Content(role="user", parts=[types.Part.from_text(text=initial_prompt)])]

        tool_cache: dict[str, tuple[str, str, int]] = {}
        while budget.has_room():
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                max_output_tokens=768,
                tools=tools,
            )
            budget.begin_round()
            response = client.models.generate_content(
                model=self.provider.model_name,
                contents=contents,
                config=config,
            )

            candidates = response.candidates or []
            if not candidates:
                result.stop_reason = "empty_model_response"
                break

            candidate = candidates[0]
            parts = candidate.content.parts if candidate.content else []

            # Check if the model made any function calls
            function_calls = [p for p in parts if p.function_call]
            if not function_calls:
                # Model decided it has enough evidence — stop
                result.stop_reason = "model_finished"
                break

            # Process each function call
            response_parts = []
            for part in function_calls:
                fc = part.function_call
                tool_name = fc.name
                args = dict(fc.args) if fc.args else {}
                cache_key = json.dumps(
                    [tool_name, args], sort_keys=True, separators=(",", ":"), default=str
                )
                cached = tool_cache.get(cache_key)
                if cached is None:
                    tool_result, summary, nbytes = self._execute_tool(tool_name, args, result)
                    tool_cache[cache_key] = (tool_result, summary, nbytes)
                    charged_bytes = nbytes
                else:
                    tool_result, summary, _ = cached
                    summary = f"Cached: {summary}"
                    charged_bytes = 0

                record = ToolCallRecord(
                    tool_name=tool_name,
                    arguments=args,
                    bytes_returned=charged_bytes,
                    result_summary=summary,
                )
                result.transcript.append(record)
                budget.consume_bytes(charged_bytes)

                response_parts.append(
                    types.Part.from_function_response(
                        name=tool_name,
                        response={"result": tool_result},
                    )
                )

                if not budget.has_room():
                    break

            # Add model response and tool results to conversation
            contents.append(candidate.content)
            contents.append(types.Content(role="user", parts=response_parts))

            if self._has_exact_evidence(result, user_request):
                result.stop_reason = "exact_evidence_found"
                logger.debug(
                    "Discovery stopped after exact evidence (api_calls=%d, bytes=%d)",
                    budget.rounds_used,
                    budget.bytes_consumed,
                )
                break

            if not budget.has_room():
                logger.debug(
                    "Discovery budget exhausted (rounds=%d, bytes=%d)",
                    budget.rounds_used,
                    budget.bytes_consumed,
                )
                result.stop_reason = "budget_exhausted"
                break

        if not result.stop_reason and not budget.has_room():
            result.stop_reason = "budget_exhausted"

    def _execute_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: DiscoveryResult,
    ) -> tuple[str, str, int]:
        """Dispatch a tool call to the appropriate workspace primitive.

        Returns (tool_result_text, summary, bytes_used).
        """
        try:
            if tool_name == "search_workspace":
                return self._exec_search(args, result)
            elif tool_name == "list_directory":
                return self._exec_list(args)
            elif tool_name == "read_file_excerpt":
                return self._exec_read(args, result)
            elif tool_name == "inspect_binary":
                return self._exec_inspect_binary(args, result)
            else:
                return f"Unknown tool: {tool_name}", f"Unknown tool: {tool_name}", 0
        except Exception as exc:
            error_msg = f"Error: {exc}"
            return error_msg, error_msg[:200], 0

    def _exec_search(self, args: dict[str, Any], result: DiscoveryResult) -> tuple[str, str, int]:
        query = str(args.get("query", ""))
        glob = str(args.get("glob", "*"))
        if not query:
            return "Error: query is required", "Empty query", 0

        matches = self.context_tools.workspace.search_text(
            query, max_results=self.context_tools.MAX_SEARCH_RESULTS, glob=glob
        )

        if not matches:
            text = f"No matches found for '{query}'"
            return text, text, len(text.encode())

        output = json.dumps(matches, ensure_ascii=False, separators=(",", ":"))
        nbytes = len(output.encode())
        file_count = len({match["file"] for match in matches})
        summary = f"Found {len(matches)} match(es) for '{query}' in {file_count} file(s)"
        return output, summary, nbytes

    def _exec_list(self, args: dict[str, Any]) -> tuple[str, str, int]:
        subdir = str(args.get("subdir", ""))
        files = self.context_tools._workspace_paths(subdir)[:200]
        output = json.dumps(files, ensure_ascii=False, separators=(",", ":"))
        nbytes = len(output.encode())
        summary = f"Listed {len(files)} file(s) under '{subdir}'"
        return output, summary, nbytes

    def _exec_read(self, args: dict[str, Any], result: DiscoveryResult) -> tuple[str, str, int]:
        path = str(args.get("path", ""))
        if not path:
            return "Error: path is required", "Empty path", 0

        content = self.context_tools.read_file_range(path)
        nbytes = len(content.encode())
        result.seen_files[path] = content

        lines = content.count("\n")
        summary = f"Read {path} ({nbytes:,} bytes, {lines} lines)"
        return content[:5000], summary, nbytes  # Truncate tool response for model

    def _exec_inspect_binary(
        self, args: dict[str, Any], result: DiscoveryResult
    ) -> tuple[str, str, int]:
        path = str(args.get("path", ""))
        if not path:
            return "Error: path is required", "Empty path", 0

        inspection = self.context_tools._inspect_binary_path(path, user_request="")
        result.binary_inspections[path] = inspection
        output = json.dumps(inspection, ensure_ascii=False, separators=(",", ":"), default=str)
        nbytes = len(output.encode())
        summary = f"Inspected binary {path} ({nbytes:,} bytes)"
        return output[:5000], summary, nbytes


def build_discovered_context(
    context_tools: AiContextTools,
    discovery: DiscoveryResult,
    *,
    user_request: str = "",
) -> dict[str, Any]:
    """Build a context dict from discovery results, same shape as build_context().

    Respects MAX_FILES as the final cap on what goes into the planning prompt.
    """
    from noir.infrastructure.filesystem.workspace import compute_file_hash

    context: dict[str, Any] = {
        "file_snippets": {},
        "file_hashes": {},
        "file_coverage": {},
        "manifest": context_tools.inspect_manifest(),
        "omitted_files": [],
        "files": context_tools.list_project_files(user_request=user_request),
        "runtime": context_tools.analysis.runtime if context_tools.analysis else "dalvik",
        "runtimes": (
            sorted(context_tools.analysis.runtimes) if context_tools.analysis else ["dalvik"]
        ),
        "binary_inspection": {},
    }

    used = 0
    max_files = context_tools.MAX_FILES
    max_context = context_tools.MAX_CONTEXT_BYTES
    files_added = 0

    # Always include AndroidManifest.xml first
    try:
        manifest_content = context_tools.read_file_range("AndroidManifest.xml")
        context["file_snippets"]["AndroidManifest.xml"] = manifest_content
        context["file_hashes"]["AndroidManifest.xml"] = compute_file_hash(
            context_tools.workspace.safe_path("AndroidManifest.xml")
        )
        context["file_coverage"]["AndroidManifest.xml"] = "full"
        used += len(manifest_content.encode())
        files_added += 1
    except (FileNotFoundError, OSError, ValueError):
        pass

    # Add binary inspections from discovery
    for path, inspection in discovery.binary_inspections.items():
        if files_added >= max_files:
            context["omitted_files"].append(path)
            continue
        encoded = json.dumps(inspection, separators=(",", ":"), default=str).encode()
        if used + len(encoded) > max_context:
            context["omitted_files"].append(path)
            continue
        used += len(encoded)
        context["binary_inspection"][path] = inspection
        context["file_hashes"][path] = compute_file_hash(context_tools.workspace.safe_path(path))
        context["file_coverage"][path] = "structured_binary_inspection"
        files_added += 1

    # Add discovered text files
    for path, content in discovery.seen_files.items():
        if files_added >= max_files:
            context["omitted_files"].append(path)
            continue
        if path in context["file_snippets"]:
            continue  # already added (e.g. manifest)
        content_bytes = len(content.encode())
        if used + content_bytes > max_context:
            context["omitted_files"].append(path)
            continue
        used += content_bytes
        try:
            context["file_hashes"][path] = compute_file_hash(
                context_tools.workspace.safe_path(path)
            )
        except (FileNotFoundError, OSError, ValueError):
            continue
        context["file_snippets"][path] = content
        context["file_coverage"][path] = "full"
        files_added += 1

    return context
