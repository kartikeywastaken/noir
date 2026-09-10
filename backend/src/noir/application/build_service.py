"""Build service — orchestrates APKTool rebuild with validation and diagnostics."""

from __future__ import annotations

import shutil
import zipfile
from contextlib import nullcontext
from datetime import UTC, datetime

from noir.domain.config import NoirConfig, get_config
from noir.domain.enums import EventSeverity, JobState, ProjectStatus, WorkflowStage
from noir.domain.models import AuditEvent, BuildResult, JobInfo
from noir.infrastructure.apktool.adapter import ApkToolAdapter
from noir.infrastructure.database.repositories import (
    BuildRepository,
    EventRepository,
    JobRepository,
    ProjectRepository,
)
from noir.infrastructure.filesystem.workspace import ProjectWorkspace, compute_file_hash
from noir.security.locking import locked_project, require_clean_workspace


class BuildServiceError(Exception):
    pass


class BuildService:
    """Orchestrates APKTool rebuild and output validation."""

    def __init__(self, config: NoirConfig | None = None):
        self.config = config or get_config()
        self.project_repo = ProjectRepository()
        self.build_repo = BuildRepository()
        self.job_repo = JobRepository()
        self.event_repo = EventRepository()
        self.apktool = ApkToolAdapter(self.config)

    @locked_project
    def build(self, project_id: str, *, job: JobInfo | None = None) -> BuildResult:
        """Build an APK from the decoded workspace.

        Returns BuildResult with paths to unsigned APK.
        """
        project = self.project_repo.get(project_id)
        if not project:
            raise BuildServiceError(f"Project not found: {project_id}")

        workspace = ProjectWorkspace(project_id, self.config)
        try:
            require_clean_workspace(self.config, project_id)
            from noir.validation.workspace_validator import ValidationService

            validation = ValidationService(self.config).validate(project_id)
            if not validation.passed:
                raise ValueError("Workspace validation failed; rebuild blocked")
        except ValueError as exc:
            raise BuildServiceError(str(exc)) from exc

        # Create build result
        build = BuildResult(
            project_id=project_id,
            workspace_revision=project.workspace_revision,
        )

        # Queue workflows provide the canonical job. Direct CLI builds retain
        # their own persisted job for backward compatibility.
        owns_job = job is None
        if job is None:
            job = JobInfo(
                project_id=project_id,
                stage=WorkflowStage.REBUILDING,
                state=JobState.RUNNING,
                started_at=datetime.now(UTC),
            )
            self.job_repo.create(job)
        elif job.project_id != project_id:
            raise BuildServiceError("Build job does not belong to this project")

        try:
            # Build directory
            build_dir = workspace.builds_dir / build.build_id
            build_dir.mkdir(parents=True, exist_ok=True)

            output_apk = build_dir / "unsigned.apk"
            framework_dir = workspace.root / "metadata" / "framework-cache"

            self.event_repo.create(
                AuditEvent(
                    project_id=project_id,
                    job_id=job.job_id,
                    stage=WorkflowStage.REBUILDING,
                    severity=EventSeverity.INFO,
                    message="Starting APKTool rebuild",
                )
            )

            # Apktool reads decoded sources but creates root-level build/dist
            # intermediates. Avoid copying tens of thousands of source files
            # when those generated paths did not exist before the build. A
            # pre-existing path gets the conservative isolated-copy fallback.
            generated_paths = [
                workspace.decoded_dir / "build",
                workspace.decoded_dir / "dist",
            ]
            direct_build = not any(path.exists() or path.is_symlink() for path in generated_paths)
            if direct_build:
                build_workspace = workspace.decoded_dir
            else:
                build_workspace = build_dir / "workspace"
                shutil.copytree(workspace.decoded_dir, build_workspace, symlinks=False)
            # Run APKTool build
            from noir.application.jobs import job_runtime

            runtime = nullcontext() if not owns_job else job_runtime(job.job_id)
            try:
                with runtime:
                    result = self.apktool.build(
                        build_workspace,
                        output_apk,
                        framework_dir=framework_dir,
                    )
            finally:
                if direct_build:
                    decoded_root = workspace.decoded_dir.resolve()
                    for path in generated_paths:
                        if not path.exists() and not path.is_symlink():
                            continue
                        if path.is_symlink() or path.parent.resolve() != decoded_root:
                            raise BuildServiceError(
                                "Apktool generated an unsafe build-artifact path; cleanup blocked"
                            )
                        shutil.rmtree(path)
                elif build_workspace.exists() or build_workspace.is_symlink():
                    if (
                        build_workspace.is_symlink()
                        or build_workspace.parent.resolve() != build_dir.resolve()
                    ):
                        raise BuildServiceError("Unsafe temporary build workspace; cleanup blocked")
                    shutil.rmtree(build_workspace)

            # Validate output
            if not output_apk.exists() or output_apk.stat().st_size == 0:
                raise BuildServiceError("Build produced no output")

            # Verify it's a valid ZIP/APK
            if not zipfile.is_zipfile(output_apk):
                raise BuildServiceError("Build output is not a valid APK container")

            # Record build info
            build.unsigned_apk_path = str(output_apk)
            build.unsigned_apk_hash = compute_file_hash(output_apk)
            build.success = True
            build.apktool_version = result.tool_version
            build.build_tools_version = self.config.build_tools_version
            build.tool_logs = result.stdout + "\n" + result.stderr

            self.build_repo.create(build)

            # Update project
            project.status = ProjectStatus.BUILT
            self.project_repo.update(project)

            if owns_job:
                job.state = JobState.SUCCEEDED
                job.finished_at = datetime.now(UTC)
                job.result_data = {
                    "build_id": build.build_id,
                    "unsigned_apk_hash": build.unsigned_apk_hash,
                }
                self.job_repo.update(job)

            self.event_repo.create(
                AuditEvent(
                    project_id=project_id,
                    job_id=job.job_id,
                    stage=WorkflowStage.REBUILDING,
                    severity=EventSeverity.INFO,
                    message=f"Build succeeded: {build.build_id}",
                    metadata={
                        "build_id": build.build_id,
                        "apktool_version": build.apktool_version,
                        "duration": result.duration_seconds,
                    },
                )
            )

            return build

        except Exception as e:
            build.success = False
            build.error_message = str(e)
            if hasattr(e, "result") and e.result:
                build.tool_logs = e.result.stdout + "\n" + e.result.stderr
            self.build_repo.create(build)

            if owns_job:
                job.state = JobState.FAILED
                job.error_message = str(e)
                job.finished_at = datetime.now(UTC)
                self.job_repo.update(job)

            self.event_repo.create(
                AuditEvent(
                    project_id=project_id,
                    job_id=job.job_id,
                    stage=WorkflowStage.REBUILDING,
                    severity=EventSeverity.ERROR,
                    message=f"Build failed: {e}",
                )
            )

            raise BuildServiceError(str(e)) from e

    def get_build(self, build_id: str) -> BuildResult | None:
        return self.build_repo.get(build_id)

    def list_builds(self, project_id: str) -> list[BuildResult]:
        return self.build_repo.list_by_project(project_id)

    def diagnose_failure(self, project_id: str, build_id: str, *, allow_ai: bool = False) -> dict:
        """Diagnose a build failure, optionally using AI."""
        build = self.build_repo.get(build_id)
        if not build or build.project_id != project_id:
            raise BuildServiceError(f"Build not found: {build_id}")
        if build.success:
            return {"diagnosis": "Build was successful, no diagnosis needed."}

        result: dict = {
            "build_id": build_id,
            "error": build.error_message,
            "tool_logs": build.tool_logs[:5000],
        }

        if allow_ai:
            try:
                from noir.infrastructure.ai.factory import create_ai_provider

                provider = create_ai_provider(self.config)
                workspace = ProjectWorkspace(project_id, self.config)
                from noir.infrastructure.ai.context import AiContextTools

                context_tools = AiContextTools(workspace)
                context = context_tools.build_context()

                diagnosis = provider.diagnose_build_failure(
                    build.tool_logs or build.error_message or "",
                    context,
                )
                result["ai_diagnosis"] = diagnosis
            except Exception as e:
                result["ai_diagnosis_error"] = str(e)

        return result
