"""Manual edit session management.

Supports terminal-based manual modification workflow:
1. `manual begin` — creates a tracked session, locks workspace
2. User edits files externally
3. `manual record` — detects changes, validates, advances revision
"""

from __future__ import annotations

from pathlib import Path

from noir.domain.config import NoirConfig, get_config
from noir.domain.enums import (
    ApprovalScope,
    EventSeverity,
    ProjectStatus,
    Provenance,
    WorkflowStage,
)
from noir.domain.models import AuditEvent, ManualEditSession
from noir.infrastructure.database.repositories import (
    ApprovalRepository,
    EventRepository,
    FileManifestRepository,
    ManualSessionRepository,
    ProjectRepository,
)
from noir.infrastructure.filesystem.workspace import ProjectWorkspace, is_binary_file
from noir.security.locking import locked_project


class ManualEditError(Exception):
    pass


class ManualService:
    """Manages manual edit sessions."""

    def __init__(self, config: NoirConfig | None = None):
        self.config = config or get_config()
        self.project_repo = ProjectRepository()
        self.session_repo = ManualSessionRepository()
        self.manifest_repo = FileManifestRepository()
        self.approval_repo = ApprovalRepository()
        self.event_repo = EventRepository()

    @locked_project
    def begin_session(self, project_id: str) -> ManualEditSession:
        """Start a new manual edit session.

        Prevents concurrent AI patching or building.
        """
        project = self.project_repo.get(project_id)
        if not project:
            raise ManualEditError(f"Project not found: {project_id}")

        # Check for existing active session
        existing = self.session_repo.get_active(project_id)
        if existing:
            raise ManualEditError(
                f"A manual edit session is already active (session {existing.session_id}). "
                "Finish or cancel it before starting a new one."
            )

        session = ManualEditSession(
            project_id=project_id,
            workspace_revision_start=project.workspace_revision,
        )
        self.session_repo.create(session)

        # Mark project as being modified
        project.status = ProjectStatus.MODIFYING
        self.project_repo.update(project)

        self.event_repo.create(
            AuditEvent(
                project_id=project_id,
                stage=WorkflowStage.APPLYING_PATCH,
                severity=EventSeverity.INFO,
                message=f"Manual edit session started (session {session.session_id})",
            )
        )

        return session

    @locked_project
    def record_changes(self, project_id: str, message: str = "") -> dict:
        """Record changes made during a manual edit session.

        Compares current workspace against baseline, detects all changes,
        advances workspace revision, and invalidates stale approvals/builds.
        """
        project = self.project_repo.get(project_id)
        if not project:
            raise ManualEditError(f"Project not found: {project_id}")

        session = self.session_repo.get_active(project_id)
        if not session:
            raise ManualEditError("No active manual edit session. Use 'noir manual begin' first.")

        workspace = ProjectWorkspace(project_id, self.config)

        # Get baseline manifest
        baseline = self.manifest_repo.get_latest(project_id)
        if not baseline:
            baseline = []

        # Detect changes
        changes = workspace.detect_changes(baseline)
        detected = changes["added"] + changes["modified"] + changes["deleted"]

        if not detected:
            self.session_repo.finish(session.session_id, [], message)
            return {
                "session_id": session.session_id,
                "changes_detected": 0,
                "message": "No changes detected in workspace.",
            }

        baseline_by_path = {entry.relative_path: entry for entry in baseline}
        unsupported = []
        for relative_path in detected:
            current = workspace.safe_path(relative_path)
            previous = baseline_by_path.get(relative_path)
            is_binary = (
                is_binary_file(current)
                if current.is_file()
                else bool(previous and previous.is_binary)
            )
            oversized = current.is_file() and current.stat().st_size > 1_000_000
            if is_binary or oversized:
                unsupported.append(relative_path)
        if unsupported:
            raise ManualEditError(
                "Manual sessions cannot record binary or >1 MB file mutations. "
                "Use an approved structured CIL, IL2CPP, or native patch for supported "
                "code binaries. Unsupported paths: " + ", ".join(unsupported[:10])
            )

        # Advance workspace revision
        new_revision = project.workspace_revision + 1
        project.workspace_revision = new_revision
        project.dirty = False
        project.status = ProjectStatus.ANALYZED
        self.project_repo.update(project)

        # Save new manifest
        new_manifest = workspace.build_file_manifest()
        self.manifest_repo.save(project_id, new_revision, new_manifest)

        # Invalidate stale approvals
        self.approval_repo.invalidate_for_project(project_id, ApprovalScope.PLAN)
        self.approval_repo.invalidate_for_project(project_id, ApprovalScope.PATCH)
        self.approval_repo.invalidate_for_project(project_id, ApprovalScope.SIGNING)

        # Finish session
        self.session_repo.finish(session.session_id, detected, message)

        # Log event
        self.event_repo.create(
            AuditEvent(
                project_id=project_id,
                stage=WorkflowStage.APPLYING_PATCH,
                severity=EventSeverity.INFO,
                message=f"Manual changes recorded: {len(detected)} files changed",
                metadata={
                    "added": changes["added"],
                    "modified": changes["modified"],
                    "deleted": changes["deleted"],
                    "provenance": Provenance.MANUAL.value,
                    "workspace_revision": new_revision,
                    "message": message,
                },
            )
        )

        from noir.validation.workspace_validator import ValidationService

        validation = ValidationService(self.config).validate(project_id)
        return {
            "session_id": session.session_id,
            "workspace_revision": new_revision,
            "validation": validation.model_dump(mode="json"),
            "changes_detected": len(detected),
            "added": changes["added"],
            "modified": changes["modified"],
            "deleted": changes["deleted"],
            "message": message or "Changes recorded.",
        }

    def get_active_session(self, project_id: str) -> ManualEditSession | None:
        """Get the active manual edit session for a project."""
        return self.session_repo.get_active(project_id)

    @locked_project
    def replace_file(self, project_id: str, relative_path: str, source_path: str) -> dict:
        """Replace a file in the decoded workspace from a local file.

        Must be within an active manual session.
        """
        project = self.project_repo.get(project_id)
        if not project:
            raise ManualEditError(f"Project not found: {project_id}")

        session = self.session_repo.get_active(project_id)
        if not session:
            raise ManualEditError("No active manual edit session.")

        workspace = ProjectWorkspace(project_id, self.config)
        target = workspace.safe_path(relative_path)
        source = Path(source_path).resolve()

        if not source.exists():
            raise ManualEditError(f"Source file not found: {source_path}")
        if not source.is_file():
            raise ManualEditError(f"Source is not a regular file: {source_path}")
        if source.stat().st_size > 1_000_000:
            raise ManualEditError(
                "Manual replacement retains the 1 MB text-file ceiling. "
                "Supported assemblies and ELF libraries require an approved structured patch."
            )
        if is_binary_file(source) or (target.is_file() and is_binary_file(target)):
            raise ManualEditError(
                "Manual binary replacement is unsupported. Use an approved CIL, IL2CPP, "
                "or native PatchOperation so hashes, validation, and audit evidence are enforced."
            )

        # Copy file
        target.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.copy2(source, target)

        return {
            "replaced": relative_path,
            "source": str(source),
        }
