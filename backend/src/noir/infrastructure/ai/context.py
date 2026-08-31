"""AI context tools — constrained workspace access for AI providers.

These tools provide bounded read access to project workspaces for AI context.
The AI must not: access the host shell, read unrelated files, approve changes,
sign/install APKs, change policy, or execute decoded code.
"""

from __future__ import annotations

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
            # Include key files by default
            paths = ["AndroidManifest.xml"]
            all_files = label_paths if label_task else context["files"]
            for f in all_files:
                if f.endswith(".smali") or f.endswith(".xml"):
                    paths.append(f)
                    if len(paths) >= self.MAX_FILES:
                        break

        used = 0
        for path in dict.fromkeys(paths[: self.MAX_FILES]):
            try:
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
