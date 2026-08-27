"""Workspace and filesystem management.

Handles project directory layout, safe path resolution, file manifests,
workspace revisions, and hash computation.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from noir.domain.config import NoirConfig
from noir.domain.models import FileManifestEntry


class WorkspaceError(Exception):
    """Raised for workspace-related errors."""

    pass


class PathSecurityError(WorkspaceError):
    """Raised when a path escapes project boundaries."""

    pass


def safe_resolve(base: Path, relative: str) -> Path:
    """Resolve a relative path within base, rejecting escapes.

    Raises PathSecurityError if the resolved path escapes base.
    """
    # Reject obvious traversal attempts
    parts = relative.replace("\\", "/").split("/")
    if any(p == ".." for p in parts):
        raise PathSecurityError(f"Path traversal rejected: {relative}")
    if relative.startswith("/") or relative.startswith("\\"):
        raise PathSecurityError(f"Absolute path rejected: {relative}")

    resolved = (base / relative).resolve()
    base_resolved = base.resolve()

    if not resolved.is_relative_to(base_resolved):
        raise PathSecurityError(f"Path escapes project boundary: {relative} -> {resolved}")

    # Check for symlink traversal
    try:
        real = resolved.resolve(strict=False)
        if not real.is_relative_to(base_resolved):
            raise PathSecurityError(f"Symlink traversal detected: {relative} -> {real}")
    except OSError:
        pass

    return resolved


def compute_file_hash(file_path: Path, algorithm: str = "sha256") -> str:
    """Compute hash of a file."""
    h = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def compute_content_hash(content: str | bytes, algorithm: str = "sha256") -> str:
    """Compute hash of content."""
    h = hashlib.new(algorithm)
    if isinstance(content, str):
        content = content.encode()
    h.update(content)
    return h.hexdigest()


def is_binary_file(file_path: Path) -> bool:
    """Heuristic check whether a file is binary."""
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(8192)
            return b"\x00" in chunk
    except OSError:
        return True


class ProjectWorkspace:
    """Manages the filesystem layout for a project.

    projects/<project-id>/
        input/          — preserved copy of the original APK
        decoded/        — APKTool decoded workspace
        changes/        — staged patches and diffs
        builds/         — build output artifacts
        reports/        — audit reports
        logs/           — process logs
        metadata/       — project metadata files
    """

    def __init__(self, project_id: str, config: NoirConfig):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", project_id):
            raise PathSecurityError("Invalid project identifier")
        self.project_id = project_id
        self.config = config
        self.root = config.projects_dir / project_id

    def create(self) -> None:
        """Create the project directory structure."""
        for subdir in ("input", "decoded", "changes", "builds", "reports", "logs", "metadata"):
            (self.root / subdir).mkdir(parents=True, exist_ok=True)

    @property
    def input_dir(self) -> Path:
        return self.root / "input"

    @property
    def decoded_dir(self) -> Path:
        return self.root / "decoded"

    @property
    def changes_dir(self) -> Path:
        return self.root / "changes"

    @property
    def builds_dir(self) -> Path:
        return self.root / "builds"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def metadata_dir(self) -> Path:
        return self.root / "metadata"

    def safe_path(self, relative: str) -> Path:
        """Resolve a relative path safely within the decoded workspace."""
        return safe_resolve(self.decoded_dir, relative)

    def store_input_apk(self, source_path: Path) -> Path:
        """Copy the source APK into the project input directory."""
        dest = self.input_dir / source_path.name
        shutil.copy2(source_path, dest)
        return dest

    def build_file_manifest(self) -> list[FileManifestEntry]:
        """Build a manifest of all files in the decoded workspace."""
        entries: list[FileManifestEntry] = []
        decoded = self.decoded_dir
        if not decoded.exists():
            return entries

        for file_path in sorted(decoded.rglob("*")):
            if file_path.is_file() and not file_path.is_symlink():
                try:
                    rel = str(file_path.relative_to(decoded))
                    sha = compute_file_hash(file_path)
                    size = file_path.stat().st_size
                    binary = is_binary_file(file_path)
                    entries.append(
                        FileManifestEntry(
                            relative_path=rel,
                            sha256=sha,
                            size=size,
                            is_binary=binary,
                        )
                    )
                except (OSError, ValueError):
                    continue

        return entries

    def detect_changes(self, baseline: list[FileManifestEntry]) -> dict:
        """Compare current workspace against a baseline manifest.

        Returns dict with added, modified, deleted file lists.
        """
        current = self.build_file_manifest()
        baseline_map = {e.relative_path: e for e in baseline}
        current_map = {e.relative_path: e for e in current}

        added = [p for p in current_map if p not in baseline_map]
        deleted = [p for p in baseline_map if p not in current_map]
        modified = [
            p
            for p in current_map
            if p in baseline_map and current_map[p].sha256 != baseline_map[p].sha256
        ]

        return {
            "added": added,
            "modified": modified,
            "deleted": deleted,
            "unchanged_count": len(current_map) - len(added) - len(modified),
        }

    def list_files(self, subdir: str = "") -> list[str]:
        """List files relative to decoded workspace."""
        base = self.decoded_dir
        if subdir:
            base = safe_resolve(self.decoded_dir, subdir)

        result: list[str] = []
        if not base.exists():
            return result

        for file_path in sorted(base.rglob("*")):
            if file_path.is_file():
                try:
                    result.append(str(file_path.relative_to(self.decoded_dir)))
                except ValueError:
                    continue
        return result

    def read_file(self, relative_path: str, max_bytes: int = 1_000_000) -> str:
        """Read file content from decoded workspace with bounds.

        Raises PathSecurityError if path escapes boundary.
        """
        file_path = self.safe_path(relative_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {relative_path}")
        if not file_path.is_file():
            raise WorkspaceError(f"Not a regular file: {relative_path}")
        size = file_path.stat().st_size
        if size > max_bytes:
            raise WorkspaceError(f"File too large to read: {size} bytes (max {max_bytes})")
        return file_path.read_text(errors="replace")

    def search_text(self, query: str, max_results: int = 100, glob: str = "*") -> list[dict]:
        """Search for text in decoded workspace files."""
        results: list[dict] = []
        decoded = self.decoded_dir
        if not decoded.exists():
            return results

        for file_path in sorted(decoded.rglob(glob)):
            if len(results) >= max_results:
                break
            if (
                file_path.is_symlink()
                or not file_path.is_file()
                or file_path.stat().st_size > 1_000_000
                or is_binary_file(file_path)
            ):
                continue
            try:
                content = file_path.read_text(errors="replace")
                for line_num, line in enumerate(content.splitlines(), 1):
                    if query in line:
                        results.append(
                            {
                                "file": str(file_path.relative_to(decoded)),
                                "line": line_num,
                                "content": line[:500],
                            }
                        )
                        if len(results) >= max_results:
                            break
            except OSError:
                continue

        return results

    def cleanup(self) -> None:
        """Remove the entire project workspace."""
        if self.root.exists():
            shutil.rmtree(self.root)
