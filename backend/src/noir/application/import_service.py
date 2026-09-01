"""Import service — orchestrates APK import, validation, decode, and initial analysis."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

from noir.domain.config import NoirConfig, get_config
from noir.domain.enums import (
    EventSeverity,
    JobState,
    ProjectStatus,
    WorkflowStage,
)
from noir.domain.models import AuditEvent, JobInfo, ProjectInfo
from noir.infrastructure.apktool.adapter import ApkToolAdapter, ApkToolError
from noir.infrastructure.database.engine import init_db
from noir.infrastructure.database.repositories import (
    EventRepository,
    FileManifestRepository,
    JobRepository,
    ProjectRepository,
)
from noir.infrastructure.filesystem.workspace import ProjectWorkspace
from noir.validation.apk_validator import ApkValidationError, validate_apk


class ApkImportError(Exception):
    """Raised when APK import fails."""

    def __init__(self, message: str, code: str = "import_error"):
        super().__init__(message)
        self.code = code


class ImportService:
    """Orchestrates APK import, validation, decode, and initial analysis."""

    def __init__(self, config: NoirConfig | None = None):
        self.config = config or get_config()
        self.project_repo = ProjectRepository()
        self.job_repo = JobRepository()
        self.event_repo = EventRepository()
        self.manifest_repo = FileManifestRepository()
        self.apktool = ApkToolAdapter(self.config)

    def _ensure_db(self) -> None:
        self.config.ensure_directories()
        init_db(self.config.effective_database_url)

    def _emit_event(
        self,
        project_id: str,
        job_id: str | None,
        stage: WorkflowStage | None,
        severity: EventSeverity,
        message: str,
        **metadata,
    ) -> None:
        self.event_repo.create(
            AuditEvent(
                project_id=project_id,
                job_id=job_id,
                stage=stage,
                severity=severity,
                message=message,
                metadata=metadata,
            )
        )

    def import_apk(
        self,
        apk_path: str | Path,
        *,
        authorized: bool = False,
        project_id: str | None = None,
        original_filename: str | None = None,
        job: JobInfo | None = None,
        input_sha256: str | None = None,
        input_size: int | None = None,
        move_input: bool = False,
    ) -> dict:
        """Import and decode an APK.

        Args:
            apk_path: Path to the APK file.
            authorized: User's authorization acknowledgment.

        Returns:
            Dict with project_id, status, and summary.

        Raises:
            ApkImportError on failure.
        """
        self._ensure_db()

        if not authorized:
            raise ApkImportError(
                "Authorization required. Use --authorized to acknowledge you have "
                "the right to decode and modify this APK.",
                "not_authorized",
            )

        apk_path = Path(apk_path).resolve()

        # Create project
        project = ProjectInfo(
            original_filename=Path(original_filename).name if original_filename else apk_path.name,
            authorization_acknowledged=True,
            authorization_timestamp=datetime.now(UTC),
        )
        if project_id:
            project.id = project_id
        ProjectWorkspace(project.id, self.config)  # Validate ID before persistence.
        self.project_repo.create(project)

        workspace = ProjectWorkspace(project.id, self.config)
        workspace.create()

        # API/queue workflows supply their canonical job. Standalone CLI imports
        # still receive a persisted job owned by this service.
        owns_job = job is None
        if job is None:
            job = JobInfo(
                project_id=project.id,
                stage=WorkflowStage.VALIDATING_INPUT,
                state=JobState.RUNNING,
                started_at=datetime.now(UTC),
            )
            self.job_repo.create(job)
        elif job.project_id != project.id:
            raise ApkImportError("Import job does not belong to this project", "invalid_job")

        try:
            # Step 1: Validate
            self._emit_event(
                project.id,
                job.job_id,
                WorkflowStage.VALIDATING_INPUT,
                EventSeverity.INFO,
                "Validating APK input",
            )

            validation = validate_apk(apk_path, self.config)
            warnings = validation.get("warnings", [])
            for warn in warnings[:10]:
                self._emit_event(
                    project.id,
                    job.job_id,
                    WorkflowStage.VALIDATING_INPUT,
                    EventSeverity.WARNING,
                    warn,
                )
            if len(warnings) > 10:
                self._emit_event(
                    project.id,
                    job.job_id,
                    WorkflowStage.VALIDATING_INPUT,
                    EventSeverity.WARNING,
                    f"{len(warnings) - 10} additional validation warnings omitted from live events",
                    warning_count=len(warnings),
                )

            # Step 2: Store input APK
            stored_apk, sha256, file_size = workspace.store_input_apk(
                apk_path,
                filename=original_filename,
                move=move_input,
                expected_hash=input_sha256,
                expected_size=input_size,
            )

            project.original_size = file_size
            project.original_sha256 = sha256
            project.status = ProjectStatus.IMPORTING
            self.project_repo.update(project)

            # Preserve a durable, private copy before expensive decoding begins.
            from noir.application.access_service import AccessService
            from noir.infrastructure.artifacts import ArtifactStore

            artifact_store = ArtifactStore(self.config)
            object_key = artifact_store.store_original(
                AccessService().project_owner(project.id),
                project.id,
                stored_apk,
                sha256=sha256,
            )

            self._emit_event(
                project.id,
                job.job_id,
                WorkflowStage.VALIDATING_INPUT,
                EventSeverity.INFO,
                f"Input APK validated: {sha256[:16]}...",
                sha256=sha256,
                size=file_size,
                classification=validation.get("classification", "unknown"),
                durable_object_key=object_key,
            )

            # Step 3: Decode with APKTool
            job.stage = WorkflowStage.DECODING
            self.job_repo.update(job)

            self._emit_event(
                project.id,
                job.job_id,
                WorkflowStage.DECODING,
                EventSeverity.INFO,
                "Starting APKTool decode",
            )

            framework_dir = workspace.root / "metadata" / "framework-cache"
            from noir.application.jobs import job_runtime

            runtime = nullcontext() if not owns_job else job_runtime(job.job_id)
            with runtime:
                decode_result = self.apktool.decode(
                    stored_apk,
                    workspace.decoded_dir,
                    framework_dir=framework_dir,
                )

            self._emit_event(
                project.id,
                job.job_id,
                WorkflowStage.DECODING,
                EventSeverity.INFO,
                f"APKTool decode completed in {decode_result.duration_seconds}s",
                apktool_version=decode_result.tool_version,
                duration=decode_result.duration_seconds,
            )

            # Save decode logs
            log_file = workspace.logs_dir / "decode.log"
            log_file.write_text(
                f"=== stdout ===\n{decode_result.stdout}\n=== stderr ===\n{decode_result.stderr}\n"
            )

            # Step 4: Build baseline file manifest
            manifest = workspace.build_file_manifest()
            self.manifest_repo.save(project.id, 0, manifest)

            # Step 5: Run analysis
            job.stage = WorkflowStage.ANALYZING
            self.job_repo.update(job)

            from noir.analysis.analyzer import AnalysisService

            analyzer = AnalysisService(self.config)
            analysis = analyzer.analyze(project.id, workspace)

            project.package_name = analysis.package_name
            project.version_name = analysis.version_name
            project.version_code = analysis.version_code
            project.status = ProjectStatus.ANALYZED
            self.project_repo.update(project)

            # The queue owns terminal state/result data for queued workflows.
            if owns_job:
                job.state = JobState.SUCCEEDED
                job.finished_at = datetime.now(UTC)
                job.result_data = {
                    "project_id": project.id,
                    "package_name": analysis.package_name,
                    "sha256": sha256,
                    "file_count": len(manifest),
                }
                self.job_repo.update(job)

            self._emit_event(
                project.id,
                job.job_id,
                WorkflowStage.ANALYZING,
                EventSeverity.INFO,
                "Import and analysis complete",
            )

            return {
                "project_id": project.id,
                "job_id": job.job_id,
                "status": "analyzed",
                "package_name": analysis.package_name,
                "version_name": analysis.version_name,
                "version_code": analysis.version_code,
                "sha256": sha256,
                "file_count": len(manifest),
                "classification": validation.get("classification", "unknown"),
                "warnings": validation.get("warnings", []),
            }

        except ApkValidationError as e:
            return self._fail_job(project, job, str(e), "validation_error", owns_job)
        except ApkToolError as e:
            return self._fail_job(project, job, str(e), "decode_error", owns_job)
        except Exception as e:
            return self._fail_job(project, job, str(e), "internal_error", owns_job)

    def _fail_job(
        self,
        project: ProjectInfo,
        job: JobInfo,
        error: str,
        code: str,
        owns_job: bool,
    ) -> dict:
        if owns_job:
            job.state = JobState.FAILED
            job.error_message = error
            job.finished_at = datetime.now(UTC)
            self.job_repo.update(job)

        project.status = ProjectStatus.FAILED
        self.project_repo.update(project)

        self._emit_event(
            project.id,
            job.job_id,
            job.stage,
            EventSeverity.ERROR,
            f"Import failed: {error}",
            error_code=code,
        )

        raise ApkImportError(error, code)
