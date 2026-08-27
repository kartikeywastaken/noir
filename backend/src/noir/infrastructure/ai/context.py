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

    MAX_FILE_SIZE = 50_000  # chars
    MAX_FILES = 20
    MAX_CONTEXT_BYTES = 45_000
    MAX_SEARCH_RESULTS = 50

    def __init__(self, workspace: ProjectWorkspace, analysis: AnalysisResult | None = None):
        self.workspace = workspace
        self.analysis = analysis

    def list_project_files(self, subdir: str = "") -> list[str]:
        """List files in the decoded workspace."""
        files = self.workspace.list_files(subdir)
        return files[:500]  # Bounded

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
        excerpts = {}
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
            "files": self.list_project_files(),
        }

        label_task = bool(
            re.search(
                r"\b(label|rename|app[ -]?name|display[ -]?name)\b|name of (?:the )?app",
                user_request,
                re.IGNORECASE,
            )
        )
        label_paths, excerpts = [], {}
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
            all_files = label_paths if label_task else self.workspace.list_files()
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
                    content = self.read_file_range(path)
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
