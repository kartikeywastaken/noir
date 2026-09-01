"""AI context tools — constrained workspace access for AI providers.

These tools provide bounded read access to project workspaces for AI context.
The AI must not: access the host shell, read unrelated files, approve changes,
sign/install APKs, change policy, or execute decoded code.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from contextlib import suppress
from typing import Any

from noir.domain.models import AnalysisResult
from noir.infrastructure.filesystem.workspace import (
    ProjectWorkspace,
    WorkspaceError,
    compute_file_hash,
)
from noir.security.xml import parse


class AiContextTools:
    """Provides constrained workspace context for AI providers."""

    MAX_FILE_SIZE = 50_000  # bytes; optional planning/discovery reads only
    MAX_PATCH_FILE_BYTES = 1_000_000  # Same bounded-text ceiling as the patch engine.
    MAX_FILES = 20
    MAX_CONTEXT_BYTES = 45_000
    MAX_INVENTORY_BYTES = 45_000
    MAX_SEARCH_RESULTS = 50
    MAX_DISASSEMBLY_BYTES = 256
    MAX_BINARY_INSPECTION_BYTES = 20_000

    def __init__(self, workspace: ProjectWorkspace, analysis: AnalysisResult | None = None):
        self.workspace = workspace
        self.analysis = analysis
        self._indexed_paths: list[str] | None = None

    def _workspace_paths(self, subdir: str = "") -> list[str]:
        """Reuse the revision manifest instead of walking the decoded tree again."""
        if self._indexed_paths is None:
            from noir.infrastructure.database.repositories import (
                FileManifestRepository,
                ProjectRepository,
            )

            try:
                project = ProjectRepository().get(self.workspace.project_id)
                manifest = (
                    FileManifestRepository().get_by_revision(
                        self.workspace.project_id, project.workspace_revision
                    )
                    if project
                    else None
                )
            except RuntimeError:
                # Context tools are also usable in isolated unit tests and
                # offline callers before a repository has been initialized.
                manifest = None
            self._indexed_paths = (
                [entry.relative_path for entry in manifest]
                if manifest is not None
                else self.workspace.list_files()
            )
        if not subdir:
            return list(self._indexed_paths)
        prefix = subdir.replace("\\", "/").strip("/") + "/"
        return [path for path in self._indexed_paths if path.startswith(prefix)]

    def list_project_files(self, subdir: str = "", *, user_request: str = "") -> list[str]:
        """Return a bounded, request-ranked inventory of real decoded paths."""
        files = self._workspace_paths(subdir)
        stop_words = {
            "add",
            "and",
            "app",
            "change",
            "from",
            "have",
            "into",
            "make",
            "modify",
            "please",
            "that",
            "the",
            "this",
            "with",
        }
        tokens = {
            token
            for token in re.findall(r"[a-z0-9_]{3,}", user_request.lower())
            if token not in stop_words
        }

        def priority(path: str) -> tuple[int, int, str]:
            lower = path.lower()
            if lower in {"androidmanifest.xml", "apktool.yml"}:
                rank = 0
            elif any(token in lower for token in tokens):
                rank = 1
            elif lower.startswith("assets/public/") and lower.count("/") <= 2:
                rank = 2
            elif lower.startswith("lib/") and lower.endswith(".so"):
                rank = 3
            elif lower.startswith("res/values") and lower.endswith(("strings.xml", "arrays.xml")):
                rank = 4
            elif lower.startswith("assets/") and lower.count("/") <= 2:
                rank = 5
            elif "/i18n/" in lower or "/fonts/" in lower:
                rank = 9
            elif lower.endswith(".smali"):
                rank = 8
            else:
                rank = 6
            return rank, lower.count("/"), lower

        result: list[str] = []
        used = 2
        for path in sorted(files, key=priority):
            encoded_size = len(path.encode("utf-8")) + 4
            if used + encoded_size > self.MAX_INVENTORY_BYTES:
                break
            result.append(path)
            used += encoded_size
        return result

    def read_file_range(self, relative_path: str, max_chars: int | None = None) -> str:
        """Read bounded content from a project file."""
        limit = min(max_chars or self.MAX_FILE_SIZE, self.MAX_FILE_SIZE)
        content = self.workspace.read_file(relative_path, max_bytes=limit)
        return content[:limit]

    def search_text(self, query: str) -> list[dict]:
        """Search for text in project files."""
        return self.workspace.search_text(query, max_results=self.MAX_SEARCH_RESULTS)

    def inspect_manifest(self) -> dict[str, Any]:
        """Get structured manifest information."""
        if self.analysis:
            return {
                "package_name": self.analysis.package_name,
                "version_name": self.analysis.version_name,
                "version_code": self.analysis.version_code,
                "min_sdk": self.analysis.min_sdk,
                "target_sdk": self.analysis.target_sdk,
                "permissions": self.analysis.permissions,
                "components": [c.model_dump() for c in self.analysis.components],
            }
        return {}

    def inspect_analysis(self) -> dict[str, Any]:
        """Get full analysis data for AI context."""
        if self.analysis:
            return self.analysis.model_dump()
        return {}

    def locate_class(self, descriptor: str) -> dict[str, Any] | None:
        """Find a Smali class by descriptor."""
        if self.analysis:
            for cls in self.analysis.smali_classes:
                if cls.descriptor == descriptor:
                    return cls.model_dump()
        return None

    def locate_method(self, class_descriptor: str, method_sig: str) -> dict[str, Any] | None:
        """Find a method in a specific class."""
        if self.analysis:
            for cls in self.analysis.smali_classes:
                if cls.descriptor == class_descriptor and method_sig in cls.methods:
                    return {
                        "class": cls.descriptor,
                        "file": cls.file_path,
                        "method": method_sig,
                    }
        return None

    @staticmethod
    def _binary_context_tokens(user_request: str) -> set[str]:
        tokens = {
            token
            for token in re.findall(r"[a-z_][a-z0-9_]{2,}", user_request.lower())
            if token
            not in {
                "add",
                "all",
                "also",
                "app",
                "change",
                "code",
                "codebase",
                "decide",
                "edited",
                "file",
                "files",
                "first",
                "gimme",
                "give",
                "go",
                "make",
                "method",
                "modify",
                "native",
                "please",
                "through",
                "then",
                "only",
                "path",
                "return",
                "the",
                "this",
                "unlimited",
                "want",
                "what",
            }
        }
        currency_terms = {"cash", "coin", "coins", "currency", "money", "wallet"}
        if tokens & currency_terms:
            tokens.update(currency_terms)
            tokens.update({"amount", "balance", "inventory", "player", "profile"})
        if tokens & {"key", "keys"}:
            tokens.update({"key", "keys"})
        return tokens

    @staticmethod
    def _symbol_terms(value: Any) -> set[str]:
        """Split managed/native identifiers without treating substrings as matches."""
        rendered = json.dumps(value, sort_keys=True, default=str)
        rendered = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", rendered)
        terms = set(re.findall(r"[a-z][a-z0-9]{1,}", rendered.lower()))
        # Treat a simple plural as equivalent while retaining the original selector.
        terms.update(term[:-1] for term in tuple(terms) if len(term) > 3 and term.endswith("s"))
        return terms

    @classmethod
    def _symbol_relevance(cls, value: Any, tokens: set[str]) -> int:
        if not tokens:
            return 0
        terms = cls._symbol_terms(value)
        normalized_tokens = set(tokens)
        normalized_tokens.update(
            token[:-1] for token in tokens if len(token) > 3 and token.endswith("s")
        )
        return len(terms & normalized_tokens)

    def _compact_assembly_inspection(
        self,
        inspection: dict[str, Any],
        user_request: str = "",
        *,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Keep selectors and hashes useful while enforcing the shared context budget."""
        tokens = self._binary_context_tokens(user_request)
        ceiling = min(
            max_bytes or self.MAX_BINARY_INSPECTION_BYTES,
            self.MAX_BINARY_INSPECTION_BYTES,
        )
        result = {key: value for key, value in inspection.items() if key not in {"types"}}
        result["types"] = []
        result["truncated"] = False

        def relevance(type_info: dict[str, Any]) -> tuple[int, str]:
            rendered = json.dumps(type_info, sort_keys=True, default=str)
            return (-self._symbol_relevance(type_info, tokens), rendered)

        ranked_types = sorted(inspection.get("types", []), key=relevance)
        for type_info in ranked_types:
            compact_type = {
                "full_name": type_info.get("full_name"),
                "fields": [],
                "methods": [],
            }
            members = [("fields", member) for member in type_info.get("fields", [])] + [
                ("methods", member) for member in type_info.get("methods", [])
            ]
            members.sort(
                key=lambda item: (
                    -self._symbol_relevance(item[1], tokens),
                    json.dumps(item[1], sort_keys=True, default=str),
                )
            )
            # A single large type must not consume the complete evidence budget.
            # Relevant selectors remain first; the fallback members preserve enough
            # surrounding structure for the model to reason about the type.
            if tokens:
                relevant_members = [
                    member for member in members if self._symbol_relevance(member[1], tokens) > 0
                ]
                fallback_members = [
                    member for member in members if self._symbol_relevance(member[1], tokens) == 0
                ][:4]
                selected_members = [*relevant_members[:16], *fallback_members]
            else:
                selected_members = members[:24]
            if len(selected_members) < len(members):
                result["truncated"] = True
            for collection, member in selected_members:
                compact_type[collection].append(member)
                candidate = {**result, "types": [*result["types"], compact_type]}
                if len(json.dumps(candidate, default=str).encode()) > ceiling:
                    compact_type[collection].pop()
                    result["truncated"] = True
                    break
            if compact_type["fields"] or compact_type["methods"]:
                result["types"].append(compact_type)
            if len(json.dumps(result, default=str).encode()) >= ceiling:
                break
        if len(result["types"]) < len(ranked_types):
            result["truncated"] = True
        result["included_type_count"] = len(result["types"])
        result["total_type_count"] = len(inspection.get("types", []))
        return result

    def _full_assembly_inspection(self, relative_path: str) -> dict[str, Any]:
        from noir.infrastructure.dotnet.adapter import inspect_assembly

        path = self.workspace.safe_path(relative_path)
        if path.suffix.lower() != ".dll":
            raise ValueError("Managed assembly inspection requires a .dll path")
        return inspect_assembly(self.workspace.config, path)

    def inspect_assembly(self, relative_path: str, *, user_request: str = "") -> dict[str, Any]:
        """Inspect one bounded managed assembly without exposing its raw bytes."""
        return self._compact_assembly_inspection(
            self._full_assembly_inspection(relative_path), user_request
        )

    def read_method_il(self, relative_path: str, type_name: str, method_sig: str) -> str:
        """Read one selected method while retaining the normal context ceiling."""
        from noir.infrastructure.dotnet.adapter import read_method_il

        result = read_method_il(
            self.workspace.config,
            self.workspace.safe_path(relative_path),
            type_name,
            method_sig,
        )
        source = str(result.get("il_source", ""))
        if len(source.encode()) > self.MAX_CONTEXT_BYTES:
            raise ValueError("Selected CIL method exceeds the AI context ceiling")
        return source

    def inspect_il2cpp_method(self, type_name: str, method_sig: str) -> dict[str, Any]:
        """Correlate one symbol-rich IL2CPP method without guessing offsets."""
        from noir.infrastructure.il2cpp.metadata import (
            Il2CppMetadata,
            Il2CppMetadataError,
        )

        metadata = sorted(self.workspace.decoded_dir.rglob("global-metadata.dat"))
        binaries = sorted(self.workspace.decoded_dir.glob("lib/*/libil2cpp.so"))
        if len(metadata) != 1 or not binaries:
            raise ValueError("IL2CPP method inspection requires one metadata file and an ABI")
        if metadata[0].stat().st_size > self.workspace.config.max_il2cpp_metadata_size:
            raise ValueError("IL2CPP metadata exceeds the configured inspection ceiling")
        results = []
        for binary in binaries:
            relative_path = binary.relative_to(self.workspace.decoded_dir).as_posix()
            try:
                if binary.stat().st_size > self.workspace.config.max_native_library_size:
                    raise Il2CppMetadataError(
                        "IL2CPP binary exceeds the configured inspection ceiling"
                    )
                results.append(
                    {
                        "relative_path": relative_path,
                        **Il2CppMetadata(metadata[0], binary).inspect_method(type_name, method_sig),
                    }
                )
            except Il2CppMetadataError as exc:
                results.append({"relative_path": relative_path, "unsupported_reason": str(exc)})
        return {
            "metadata_path": metadata[0].relative_to(self.workspace.decoded_dir).as_posix(),
            "abi_results": results,
        }

    def disassemble_native(self, relative_path: str, offset: int, length: int) -> list[dict]:
        """Return a tightly bounded instruction window for human/AI review."""
        from noir.infrastructure.native.adapter import disassemble_range

        if length > self.MAX_DISASSEMBLY_BYTES:
            raise ValueError("Native disassembly request exceeds the 256-byte context ceiling")
        return disassemble_range(self.workspace.safe_path(relative_path), offset, length)

    def _inspect_binary_path(self, relative_path: str, *, user_request: str = "") -> dict[str, Any]:
        path = self.workspace.safe_path(relative_path)
        if path.suffix.lower() == ".dll":
            from noir.infrastructure.dotnet.adapter import CilToolError

            inspection = {
                "format": "cil",
                **self._compact_assembly_inspection(
                    self._full_assembly_inspection(relative_path),
                    user_request,
                    max_bytes=14_000,
                ),
            }
            tokens = self._binary_context_tokens(user_request)
            candidates = sorted(
                [
                    (
                        self._symbol_relevance(
                            {
                                "type": type_info.get("full_name", ""),
                                "method": method.get("signature", ""),
                            },
                            tokens,
                        ),
                        type_info.get("full_name", ""),
                        method.get("signature", ""),
                    )
                    for type_info in inspection.get("types", [])
                    for method in type_info.get("methods", [])
                    if method.get("has_body")
                    if not tokens
                    or self._symbol_relevance(
                        {
                            "type": type_info.get("full_name", ""),
                            "method": method.get("signature", ""),
                        },
                        tokens,
                    )
                ],
                key=lambda item: (-item[0], item[1], item[2]),
            )[:3]
            inspection["selected_method_il"] = []
            for _score, type_name, method_signature in candidates:
                try:
                    source = self.read_method_il(relative_path, type_name, method_signature)
                except (ValueError, CilToolError):
                    continue
                entry = {
                    "type_full_name": type_name,
                    "method_signature": method_signature,
                    "il_source": source,
                }
                candidate = {
                    **inspection,
                    "selected_method_il": [
                        *inspection["selected_method_il"],
                        entry,
                    ],
                }
                if (
                    len(json.dumps(candidate, default=str).encode())
                    > self.MAX_BINARY_INSPECTION_BYTES
                ):
                    break
                inspection["selected_method_il"].append(entry)
            return inspection
        if path.suffix.lower() == ".so":
            from noir.infrastructure.native.adapter import (
                NativePatchError,
                disassemble_range,
                inspect_elf,
            )

            if path.stat().st_size > self.workspace.config.max_native_library_size:
                raise ValueError("ELF exceeds the configured binary inspection ceiling")
            inspection = inspect_elf(path)
            tokens = self._binary_context_tokens(user_request)

            def rank(value: Any) -> tuple[int, str]:
                rendered = json.dumps(value, sort_keys=True, default=str).lower()
                return (-sum(token in rendered for token in tokens), rendered)

            bounded = {
                key: value
                for key, value in inspection.items()
                if key not in {"exports", "imports", "symbol_details", "sections"}
            }
            for key, limit in (
                ("symbol_details", 128),
                ("exports", 256),
                ("imports", 128),
                ("sections", 128),
            ):
                bounded[key] = sorted(inspection.get(key, []), key=rank)[:limit]
            bounded["truncated"] = any(
                len(bounded[key]) < len(inspection.get(key, []))
                for key in ("symbol_details", "exports", "imports", "sections")
            )
            while len(json.dumps(bounded, default=str).encode()) > self.MAX_BINARY_INSPECTION_BYTES:
                largest = max(
                    (key for key in ("exports", "imports", "symbol_details", "sections")),
                    key=lambda key: len(json.dumps(bounded[key], default=str)),
                )
                if not bounded[largest]:
                    raise ValueError("ELF inspection metadata exceeds the context ceiling")
                bounded[largest].pop()
                bounded["truncated"] = True
            bounded["selected_disassembly"] = []
            for symbol in sorted(inspection.get("symbol_details", []), key=rank):
                length = int(symbol.get("size", 0))
                if length <= 0 or length > self.MAX_DISASSEMBLY_BYTES:
                    continue
                try:
                    instructions = disassemble_range(
                        path,
                        int(symbol["file_offset"]),
                        length,
                        abi=inspection["abi"],
                    )
                except (KeyError, NativePatchError, TypeError, ValueError):
                    continue
                entry = {
                    "name": symbol.get("name"),
                    "file_offset": symbol.get("file_offset"),
                    "size": length,
                    "sha256": symbol.get("sha256"),
                    "instructions": instructions,
                }
                candidate = {
                    **bounded,
                    "selected_disassembly": [
                        *bounded["selected_disassembly"],
                        entry,
                    ],
                }
                if (
                    len(json.dumps(candidate, default=str).encode())
                    > self.MAX_BINARY_INSPECTION_BYTES
                ):
                    break
                bounded["selected_disassembly"].append(entry)
                if len(bounded["selected_disassembly"]) >= 3:
                    break
            if path.name == "libil2cpp.so":
                from noir.infrastructure.il2cpp.metadata import (
                    Il2CppMetadata,
                    Il2CppMetadataError,
                )

                metadata = sorted(self.workspace.decoded_dir.rglob("global-metadata.dat"))
                try:
                    if len(metadata) != 1:
                        raise Il2CppMetadataError(
                            f"Expected one global-metadata.dat, found {len(metadata)}"
                        )
                    if metadata[0].stat().st_size > self.workspace.config.max_il2cpp_metadata_size:
                        raise Il2CppMetadataError(
                            "IL2CPP metadata exceeds the configured inspection ceiling"
                        )
                    summary = Il2CppMetadata(metadata[0], path).search_strings(user_request)
                    candidate = {**bounded, "il2cpp_metadata": summary}
                    if (
                        len(json.dumps(candidate, default=str).encode())
                        <= self.MAX_BINARY_INSPECTION_BYTES
                    ):
                        bounded["il2cpp_metadata"] = summary
                except (OSError, Il2CppMetadataError) as exc:
                    bounded["il2cpp_metadata_error"] = str(exc)
            result = {"format": "elf", **bounded}
            while len(json.dumps(result, default=str).encode()) > self.MAX_BINARY_INSPECTION_BYTES:
                trimmed = False
                for key in (
                    "selected_disassembly",
                    "symbol_details",
                    "exports",
                    "imports",
                    "sections",
                ):
                    if result.get(key):
                        result[key].pop()
                        result["truncated"] = True
                        trimmed = True
                        break
                if trimmed:
                    continue
                if "il2cpp_metadata" in result:
                    result.pop("il2cpp_metadata")
                    result["truncated"] = True
                    continue
                if "il2cpp_metadata_error" in result:
                    result.pop("il2cpp_metadata_error")
                    result["truncated"] = True
                    continue
                raise ValueError("ELF inspection metadata exceeds the context ceiling")
            return result
        raise ValueError("Unsupported binary context type")

    def _label_evidence(self) -> tuple[list[str], dict[str, str]]:
        """Find actual application/launcher labels and exact referenced string elements."""
        namespace = "{http://schemas.android.com/apk/res/android}"
        manifest = self.workspace.safe_path("AndroidManifest.xml")
        if not manifest.is_file():
            return [], {}
        root = parse(manifest).getroot()
        app = root.find("application")
        if app is None:
            return [], {}
        labels = [app.get(namespace + "label", "")]
        for element in app:
            if element.tag not in ("activity", "activity-alias"):
                continue
            if any(
                category.get(namespace + "name") == "android.intent.category.LAUNCHER"
                for category in element.findall("intent-filter/category")
            ):
                labels.append(element.get(namespace + "label", ""))
        names = {label.removeprefix("@string/") for label in labels if label.startswith("@string/")}
        excerpts: dict[str, str] = {}
        if not names:
            return [], excerpts
        candidates = sorted(
            self.workspace.decoded_dir.glob("res/values*/strings.xml"),
            key=lambda p: (p.parent.name != "values", str(p)),
        )
        # Read a bounded number of resource files; never expose unrelated entries to the model.
        for candidate in candidates[:50]:
            relative = candidate.relative_to(self.workspace.decoded_dir).as_posix()
            target = self.workspace.safe_path(relative)
            if target.stat().st_size > 1_000_000:
                continue
            content = target.read_text(encoding="utf-8")
            matches = []
            for match in re.finditer(r"<string\b[^>]*>.*?</string\s*>", content, re.DOTALL):
                name = re.search(r"""\bname\s*=\s*["']([^"']+)["']""", match.group())
                if name and name.group(1) in names:
                    matches.append(match.group())
            if matches:
                excerpts[relative] = "\n".join(matches)
        return list(excerpts), excerpts

    def build_context(
        self, file_paths: list[str] | None = None, *, user_request: str = ""
    ) -> dict[str, Any]:
        """Build a bounded context dict for AI provider calls.

        Args:
            file_paths: Specific files to include. If None, includes key files.

        Returns:
            Context dict with file snippets and analysis data.
        """
        context: dict[str, Any] = {
            "file_snippets": {},
            "file_hashes": {},
            "file_coverage": {},
            "manifest": self.inspect_manifest(),
            "omitted_files": [],
            "files": self.list_project_files(user_request=user_request),
            "runtime": self.analysis.runtime if self.analysis else "dalvik",
            "binary_inspection": {},
        }

        label_task = bool(
            re.search(
                r"\b(label|rename|app[ -]?name|display[ -]?name)\b|name of (?:the )?app",
                user_request,
                re.IGNORECASE,
            )
        )
        label_paths: list[str] = []
        excerpts: dict[str, str] = {}
        if label_task:
            # Resource discovery is optional; the full manifest remains primary evidence.
            with suppress(OSError, ValueError, ET.ParseError, WorkspaceError):
                label_paths, excerpts = self._label_evidence()
            context["task_focus"] = "application_and_launcher_labels"
            context["files"] = ["AndroidManifest.xml", *label_paths]
        paths = list(dict.fromkeys(file_paths or []))
        if file_paths is not None and len(paths) > self.MAX_FILES:
            raise ValueError("Approved plan exceeds the AI file limit; split it into smaller plans")
        if not paths:
            # Binary runtimes need structured code evidence during discovery. Reserve
            # those slots before XML/Smali can consume the complete file allowance.
            paths = ["AndroidManifest.xml"]
            if self.analysis and self.analysis.runtime in {"mono", "il2cpp", "native_only"}:
                binary_paths = [
                    *self.analysis.managed_assemblies,
                    *[
                        f"lib/{entry.abi}/{library}"
                        for entry in self.analysis.native_libs
                        for library in entry.libraries
                    ],
                ]
                request_tokens = self._binary_context_tokens(user_request)

                def binary_priority(path: str) -> tuple[int, int, int, str]:
                    basename = path.rsplit("/", 1)[-1].lower()
                    if self.analysis and self.analysis.runtime == "mono":
                        runtime_rank = {
                            "assembly-csharp.dll": 0,
                            "assembly-csharp-firstpass.dll": 1,
                        }.get(basename, 2 if basename.endswith(".dll") else 4)
                    elif self.analysis and self.analysis.runtime == "il2cpp":
                        runtime_rank = 0 if basename == "libil2cpp.so" else 3
                    else:
                        runtime_rank = 0 if basename == "libmain.so" else 2
                    return (
                        runtime_rank,
                        -self._symbol_relevance(path, request_tokens),
                        path.count("/"),
                        path.lower(),
                    )

                for path in sorted(dict.fromkeys(binary_paths), key=binary_priority)[:2]:
                    if path not in paths:
                        paths.append(path)

            # Fill the remaining discovery slots with key text files.
            all_files = label_paths if label_task else context["files"]
            for f in all_files:
                if (f.endswith(".smali") or f.endswith(".xml")) and f not in paths:
                    paths.append(f)
                    if len(paths) >= self.MAX_FILES:
                        break

        used = 0
        for path in dict.fromkeys(paths[: self.MAX_FILES]):
            try:
                target = self.workspace.safe_path(path)
                if target.suffix.lower() in {".dll", ".so"}:
                    inspection = self._inspect_binary_path(path, user_request=user_request)
                    encoded = json.dumps(inspection, separators=(",", ":")).encode()
                    if used + len(encoded) > self.MAX_CONTEXT_BYTES:
                        context["omitted_files"].append(path)
                        continue
                    used += len(encoded)
                    context["binary_inspection"][path] = inspection
                    context["file_hashes"][path] = compute_file_hash(target)
                    context["file_coverage"][path] = "structured_binary_inspection"
                    continue
                coverage = "full"
                try:
                    if file_paths is None or path in excerpts:
                        content = self.read_file_range(path)
                    else:
                        # An approved file is required evidence, not a discovery snippet.
                        # The 50KB planning cap can reject a complete patch request that
                        # fits the configured budget. Read it intact; bounded_prompt then
                        # counts JSON escaping, instructions, schema and ALL required files.
                        limit = min(
                            self.workspace.config.ai_max_request_size, self.MAX_PATCH_FILE_BYTES
                        )
                        content = self.workspace.read_file(path, max_bytes=limit)
                except WorkspaceError:
                    if path not in excerpts:
                        raise
                    content = excerpts[path]
                    coverage = "exact_label_elements_only"
                # Required patch files are never silently dropped. The final prompt budget
                # either accommodates them or fails before contacting the provider.
                if file_paths is None and used + len(content.encode()) > self.MAX_CONTEXT_BYTES:
                    context["omitted_files"].append(path)
                    continue
                used += len(content.encode())
                context["file_snippets"][path] = content
                context["file_hashes"][path] = compute_file_hash(self.workspace.safe_path(path))
                context["file_coverage"][path] = coverage
            except (FileNotFoundError, OSError, ValueError, WorkspaceError):
                if file_paths is not None and self.workspace.safe_path(path).exists():
                    raise ValueError(
                        f"Required patch file cannot be read within safe limits: {path}. "
                        "Use a smaller plan or manual editing."
                    ) from None
                context["omitted_files"].append(path)
                continue

        return context
