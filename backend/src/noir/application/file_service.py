"""File inspection service for decoded APK workspaces."""

from __future__ import annotations

from noir.domain.config import NoirConfig, get_config
from noir.infrastructure.database.repositories import ProjectRepository
from noir.infrastructure.filesystem.workspace import ProjectWorkspace


class FileService:
    """Provides safe file listing, reading, and search within project workspaces."""

    def __init__(self, config: NoirConfig | None = None):
        self.config = config or get_config()
        self.project_repo = ProjectRepository()

    def _get_workspace(self, project_id: str) -> ProjectWorkspace:
        project = self.project_repo.get(project_id)
        if not project:
            raise ValueError(f"Project not found: {project_id}")
        return ProjectWorkspace(project_id, self.config)

    def list_files(self, project_id: str, subdir: str = "") -> list[str]:
        """List files in the decoded workspace."""
        ws = self._get_workspace(project_id)
        return ws.list_files(subdir)

    def read_file(self, project_id: str, path: str, max_bytes: int = 1_000_000) -> str:
        """Read file content from decoded workspace."""
        ws = self._get_workspace(project_id)
        return ws.read_file(path, max_bytes)

    def search(self, project_id: str, query: str, max_results: int = 100) -> list[dict]:
        """Search for text in decoded workspace."""
        ws = self._get_workspace(project_id)
        return ws.search_text(query, max_results)

    def get_workspace_path(self, project_id: str) -> str:
        """Get the filesystem path to the decoded workspace."""
        ws = self._get_workspace(project_id)
        return str(ws.decoded_dir)
