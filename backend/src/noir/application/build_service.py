"""Build service — orchestrates APKTool rebuild with validation and diagnostics."""

from __future__ import annotations

import re
import shutil
import zipfile
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from noir.domain.config import NoirConfig, get_config
from noir.domain.enums import EventSeverity, JobState, ProjectStatus, WorkflowStage
from noir.domain.models import AuditEvent, BuildResult, JobInfo
from noir.infrastructure.database.repositories import (
    BuildRepository,
    EventRepository,
    JobRepository,
    ProjectRepository,
)
from noir.infrastructure.filesystem.workspace import ProjectWorkspace, compute_file_hash
from noir.infrastructure.tools.apktool import (
    ApkToolAdapter,
    ApkToolError,
    overlay_preserved_entries,
    sanitize_host_metadata,
)
from noir.infrastructure.tools.diagnostic_parser import (
    CompilerDiagnostic,
    classify_failure_shape,
    parse_diagnostics,
)
from noir.patches.smali_utils import sanitize_smali_content
from noir.security.locking import locked_project, require_clean_workspace


class BuildServiceError(Exception):
    pass


class BuildRepairExhaustedError(BuildServiceError):
    """Raised when the Two-Strike Rule halts build repair."""

    def __init__(
        self,
        message: str,
        failure_shape: str = "unknown",
        strikes: int = 0,
    ):
        super().__init__(message)
        self.failure_shape = failure_shape
        self.strikes = strikes


class BuildService:
    """Orchestrates APKTool rebuild and output validation."""

    MAX_STRIKES_PER_SHAPE = 2
    MAX_TOTAL_BUILD_ATTEMPTS = 5

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
            hybrid_build = project.execution_profile == "hybrid_manifest_only"
            direct_build = not hybrid_build and not any(
                path.exists() or path.is_symlink() for path in generated_paths
            )
            if direct_build:
                build_workspace = workspace.decoded_dir
            else:
                build_workspace = build_dir / "workspace"
                shutil.copytree(workspace.decoded_dir, build_workspace, symlinks=False)

            original_apk = None
            if project.execution_profile == "hybrid_manifest_only":
                inputs = [
                    path
                    for path in workspace.input_dir.iterdir()
                    if path.is_file() and not path.is_symlink()
                ]
                if len(inputs) != 1:
                    raise BuildServiceError(
                        "Hybrid build requires exactly one preserved original APK"
                    )
                original_apk = inputs[0]
            # Run APKTool build
            from noir.application.jobs import job_runtime

            runtime = nullcontext() if not owns_job else job_runtime(job.job_id)
            try:
                with runtime:
                    strikes: dict[str, int] = {}
                    repair_history: list[dict[str, Any]] = []
                    result = None
                    attempt = 0
                    max_total_attempts = self.MAX_TOTAL_BUILD_ATTEMPTS

                    while attempt < max_total_attempts:
                        attempt += 1
                        build.attempt_number = attempt
                        output_apk.unlink(missing_ok=True)
                        try:
                            result = self.apktool.build(
                                build_workspace,
                                output_apk,
                                framework_dir=framework_dir,
                            )
                            if original_apk is not None:
                                self.apktool.compile_manifest(
                                    original_apk,
                                    build_workspace / "AndroidManifest.xml",
                                    output_apk,
                                )
                                overlay_preserved_entries(original_apk, output_apk)
                            break
                        except ApkToolError as exc:
                            output_text = (
                                (exc.result.stderr + "\n" + exc.result.stdout)
                                if exc.result
                                else str(exc)
                            )
                            diagnostics = parse_diagnostics(output_text, default_tool="apktool")
                            primary_diag = diagnostics[0] if diagnostics else None
                            failure_shape = (
                                primary_diag.failure_shape
                                if primary_diag
                                else classify_failure_shape(output_text)
                            )

                            build.failure_info = self.apktool.structured_failure(exc.result)
                            build.failure_info["failure_shape"] = failure_shape
                            if primary_diag:
                                build.failure_info["primary_diagnostic"] = {
                                    "file": primary_diag.file_path,
                                    "line": primary_diag.line_number,
                                    "column": primary_diag.column_number,
                                    "column_end": primary_diag.column_end,
                                    "message": primary_diag.message,
                                    "severity": primary_diag.severity,
                                    "source_tool": primary_diag.source_tool,
                                    "failure_shape": primary_diag.failure_shape,
                                }

                            strikes[failure_shape] = strikes.get(failure_shape, 0) + 1
                            current_strikes = strikes[failure_shape]
                            build.failure_info["strikes"] = dict(strikes)

                            # Two-Strike Rule: stop immediately if strikes > MAX_STRIKES_PER_SHAPE
                            if current_strikes > self.MAX_STRIKES_PER_SHAPE:
                                build.retryable = False
                                msg = (
                                    f"Two-Strike Rule triggered: failure shape '{failure_shape}' "
                                    f"exceeded maximum allowed attempts "
                                    f"(strikes: {current_strikes}). Build aborted."
                                )
                                self.event_repo.create(
                                    AuditEvent(
                                        project_id=project_id,
                                        job_id=job.job_id,
                                        stage=WorkflowStage.REBUILDING,
                                        severity=EventSeverity.ERROR,
                                        message=msg,
                                        metadata={
                                            "failure_shape": failure_shape,
                                            "strike_count": current_strikes,
                                            "strikes": strikes,
                                            "repair_history": repair_history,
                                            "primary_diagnostic": build.failure_info.get(
                                                "primary_diagnostic"
                                            ),
                                        },
                                    )
                                )
                                self._rollback_workspace(workspace, build_workspace, build_dir)
                                raise BuildRepairExhaustedError(
                                    msg,
                                    failure_shape=failure_shape,
                                    strikes=current_strikes,
                                ) from exc

                            # If strike count <= 2: invoke targeted repair handlers
                            self.event_repo.create(
                                AuditEvent(
                                    project_id=project_id,
                                    job_id=job.job_id,
                                    stage=WorkflowStage.REBUILDING,
                                    severity=EventSeverity.WARNING,
                                    message=(
                                        f"Build attempt {attempt} failed with shape "
                                        f"'{failure_shape}' (strike {current_strikes}/"
                                        f"{self.MAX_STRIKES_PER_SHAPE}). Invoking repair handler."
                                    ),
                                    metadata={
                                        "attempt": attempt,
                                        "failure_shape": failure_shape,
                                        "strike_count": current_strikes,
                                        "diagnostic": primary_diag.message
                                        if primary_diag
                                        else str(exc)[:200],
                                    },
                                )
                            )

                            if build_workspace == workspace.decoded_dir:
                                build_workspace = build_dir / "workspace"
                                if build_workspace.exists():
                                    shutil.rmtree(build_workspace, ignore_errors=True)
                                shutil.copytree(
                                    workspace.decoded_dir,
                                    build_workspace,
                                    symlinks=False,
                                )

                            repaired = self.repair_failure(
                                build_workspace=build_workspace,
                                failure_shape=failure_shape,
                                primary_diagnostic=primary_diag,
                                all_diagnostics=diagnostics,
                                raw_output=output_text,
                            )

                            repair_history.append(
                                {
                                    "attempt": attempt,
                                    "failure_shape": failure_shape,
                                    "strike": current_strikes,
                                    "repaired": repaired,
                                }
                            )

                            if not repaired:
                                build.retryable = False
                                self._rollback_workspace(workspace, build_workspace, build_dir)
                                raise exc

                            for generated in (
                                build_workspace / "build",
                                build_workspace / "dist",
                            ):
                                if generated.is_dir() and not generated.is_symlink():
                                    shutil.rmtree(generated, ignore_errors=True)

                    if result is None:
                        raise BuildServiceError("APKTool did not return a build result")
            finally:
                decoded_root = workspace.decoded_dir.resolve()
                for path in generated_paths:
                    if (
                        (path.exists() or path.is_symlink())
                        and not path.is_symlink()
                        and path.parent.resolve() == decoded_root
                    ):
                        shutil.rmtree(path, ignore_errors=True)

                if (
                    build_workspace != workspace.decoded_dir
                    and (build_workspace.exists() or build_workspace.is_symlink())
                    and not build_workspace.is_symlink()
                    and build_workspace.parent.resolve() == build_dir.resolve()
                ):
                    shutil.rmtree(build_workspace, ignore_errors=True)

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
            build.retryable = False
            build.failure_info = {}
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
                build.failure_info = self.apktool.structured_failure(e.result)
            build.retryable = False
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
                    metadata={
                        "build_id": build.build_id,
                        "attempt_number": build.attempt_number,
                        "failure": build.failure_info,
                        "tool_logs": build.tool_logs,
                    },
                )
            )

            if isinstance(e, BuildServiceError):
                raise e
            raise BuildServiceError(str(e)) from e

    def _rollback_workspace(
        self,
        workspace: ProjectWorkspace,
        build_workspace: Path,
        build_dir: Path,
    ) -> None:
        """Roll back temporary build workspace on fatal failure or Two-Strike abort."""
        if (
            build_workspace != workspace.decoded_dir
            and (build_workspace.exists() or build_workspace.is_symlink())
            and not build_workspace.is_symlink()
            and build_workspace.parent.resolve() == build_dir.resolve()
        ):
            shutil.rmtree(build_workspace, ignore_errors=True)
        for generated in (workspace.decoded_dir / "build", workspace.decoded_dir / "dist"):
            if generated.is_dir() and not generated.is_symlink():
                shutil.rmtree(generated, ignore_errors=True)

    def repair_failure(
        self,
        build_workspace: Path,
        failure_shape: str,
        primary_diagnostic: CompilerDiagnostic | None,
        all_diagnostics: list[CompilerDiagnostic] | None = None,
        raw_output: str = "",
    ) -> bool:
        """Dispatch targeted repair handler based on classified failure shape."""
        handlers = {
            "host_pollution": self._repair_host_pollution,
            "missing_return_descriptor": self._repair_missing_return_descriptor,
            "duplicate_attribute": self._repair_duplicate_attribute,
            "missing_resource_attr": self._repair_missing_resource_attr,
            "invalid_xml_syntax": self._repair_invalid_xml_syntax,
            "duplicate_resource": self._repair_duplicate_resource,
            "undefined_resource": self._repair_undefined_resource,
        }
        handler = handlers.get(failure_shape)
        if handler is None:
            return False

        return handler(
            build_workspace=build_workspace,
            primary_diagnostic=primary_diagnostic,
            all_diagnostics=all_diagnostics or [],
            raw_output=raw_output,
        )

    def _repair_host_pollution(
        self,
        build_workspace: Path,
        primary_diagnostic: CompilerDiagnostic | None,
        all_diagnostics: list[CompilerDiagnostic],
        raw_output: str = "",
    ) -> bool:
        """Strip macOS extended attributes, .DS_Store, and AppleDouble (._*) files."""
        sanitize_host_metadata(build_workspace)
        if primary_diagnostic and primary_diagnostic.file_path:
            target = build_workspace / primary_diagnostic.file_path
            if target.exists() and (target.name.startswith("._") or target.name == ".DS_Store"):
                target.unlink(missing_ok=True)
        return True

    def _repair_missing_return_descriptor(
        self,
        build_workspace: Path,
        primary_diagnostic: CompilerDiagnostic | None,
        all_diagnostics: list[CompilerDiagnostic],
        raw_output: str = "",
    ) -> bool:
        """Sanitize Dalvik Smali instructions with missing void return descriptors."""
        modified = False
        if primary_diagnostic and primary_diagnostic.file_path:
            target = build_workspace / primary_diagnostic.file_path
            if target.is_file():
                content = target.read_text(encoding="utf-8", errors="replace")
                sanitized = sanitize_smali_content(content)
                if sanitized != content:
                    target.write_text(sanitized, encoding="utf-8")
                    return True

        for smali_file in build_workspace.rglob("*.smali"):
            if smali_file.is_file():
                content = smali_file.read_text(encoding="utf-8", errors="replace")
                sanitized = sanitize_smali_content(content)
                if sanitized != content:
                    smali_file.write_text(sanitized, encoding="utf-8")
                    modified = True
        return modified or True

    def _repair_duplicate_attribute(
        self,
        build_workspace: Path,
        primary_diagnostic: CompilerDiagnostic | None,
        all_diagnostics: list[CompilerDiagnostic],
        raw_output: str = "",
    ) -> bool:
        """Remove duplicate XML attributes from manifest or resource layouts."""
        target_files: list[Path] = []
        if primary_diagnostic and primary_diagnostic.file_path:
            f = build_workspace / primary_diagnostic.file_path
            if f.is_file():
                target_files.append(f)
        manifest = build_workspace / "AndroidManifest.xml"
        if manifest.is_file() and manifest not in target_files:
            target_files.append(manifest)

        modified = False
        for xml_file in target_files:
            content = xml_file.read_text(encoding="utf-8", errors="replace")

            def dedupe_tag_attribs(tag_match: re.Match) -> str:
                tag_text = tag_match.group(0)
                seen_attrs: set[str] = set()
                attr_pattern = re.compile(r'([a-zA-Z0-9_:]+)="[^"]*"')
                parts: list[str] = []
                last_end = 0
                for m in attr_pattern.finditer(tag_text):
                    attr_name = m.group(1)
                    parts.append(tag_text[last_end : m.start()])
                    if attr_name not in seen_attrs:
                        seen_attrs.add(attr_name)
                        parts.append(m.group(0))
                    last_end = m.end()
                parts.append(tag_text[last_end:])
                return "".join(parts)

            new_content = re.sub(
                r"<[a-zA-Z0-9_:-]+(?:\s+[^>]*?)?>",
                dedupe_tag_attribs,
                content,
            )
            if new_content != content:
                xml_file.write_text(new_content, encoding="utf-8")
                modified = True
        return modified or True

    def _repair_missing_resource_attr(
        self,
        build_workspace: Path,
        primary_diagnostic: CompilerDiagnostic | None,
        all_diagnostics: list[CompilerDiagnostic],
        raw_output: str = "",
    ) -> bool:
        """Inject missing attributes (e.g. android:name) into manifest components."""
        target = build_workspace / "AndroidManifest.xml"
        if primary_diagnostic and primary_diagnostic.file_path:
            f = build_workspace / primary_diagnostic.file_path
            if f.is_file():
                target = f

        if not target.is_file():
            return False

        content = target.read_text(encoding="utf-8", errors="replace")
        msg = primary_diagnostic.message.lower() if primary_diagnostic else ""

        if "android:name" in msg or "name" in msg:

            def inject_name(m: re.Match) -> str:
                elem = m.group(0)
                if 'android:name="' not in elem:
                    idx = elem.find(" ")
                    if idx > 0:
                        return (
                            elem[: idx + 1]
                            + 'android:name=".PlaceholderComponent" '
                            + elem[idx + 1 :]
                        )
                    return elem[:-1] + ' android:name=".PlaceholderComponent">'
                return elem

            new_content = re.sub(
                r"<(?:activity|service|receiver|provider)[^>]*>",
                inject_name,
                content,
            )
            if new_content != content:
                target.write_text(new_content, encoding="utf-8")
                return True

        attr_match = re.search(r'["\']([a-zA-Z0-9_:]+)["\']', msg)
        if attr_match:
            missing_attr = attr_match.group(1)
            default_val = "false" if "export" in missing_attr else ".Placeholder"

            def inject_attr(m: re.Match) -> str:
                elem = m.group(0)
                if f'{missing_attr}="' not in elem:
                    return elem[:-1] + f' {missing_attr}="{default_val}">'
                return elem

            new_content = re.sub(
                r"<(?:activity|service|receiver|provider)[^>]*>",
                inject_attr,
                content,
            )
            if new_content != content:
                target.write_text(new_content, encoding="utf-8")
                return True

        return True

    def _repair_invalid_xml_syntax(
        self,
        build_workspace: Path,
        primary_diagnostic: CompilerDiagnostic | None,
        all_diagnostics: list[CompilerDiagnostic],
        raw_output: str = "",
    ) -> bool:
        """Repair unescaped entities or binary AppleDouble headers in XML files."""
        if not primary_diagnostic or not primary_diagnostic.file_path:
            return False
        target = build_workspace / primary_diagnostic.file_path
        if not target.is_file():
            return False

        raw_bytes = target.read_bytes()
        if raw_bytes.startswith(b"\x00\x05\x16\x07") or b"\x00" in raw_bytes[:100]:
            target.write_text(
                '<?xml version="1.0" encoding="utf-8"?>\n<resources></resources>\n',
                encoding="utf-8",
            )
            return True

        text = raw_bytes.decode("utf-8", errors="replace")
        fixed = re.sub(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", "&amp;", text)
        if fixed != text:
            target.write_text(fixed, encoding="utf-8")
            return True
        return True

    def _repair_duplicate_resource(
        self,
        build_workspace: Path,
        primary_diagnostic: CompilerDiagnostic | None,
        all_diagnostics: list[CompilerDiagnostic],
        raw_output: str = "",
    ) -> bool:
        """Deduplicate resource entries in strings.xml or values resources."""
        target = build_workspace / "res" / "values" / "strings.xml"
        if primary_diagnostic and primary_diagnostic.file_path:
            f = build_workspace / primary_diagnostic.file_path
            if f.is_file():
                target = f

        if not target.is_file():
            return False

        content = target.read_text(encoding="utf-8", errors="replace")
        res_match = re.search(
            r"['\"](?:string/)?([a-zA-Z0-9_]+)['\"]",
            primary_diagnostic.message if primary_diagnostic else "",
        )
        if res_match:
            res_name = res_match.group(1)
            pattern = re.compile(
                rf'<string\s+name=["\']{re.escape(res_name)}["\'][^>]*>.*?</string>',
                re.DOTALL,
            )
            matches = list(pattern.finditer(content))
            if len(matches) > 1:
                new_content = content
                for m in reversed(matches[1:]):
                    new_content = new_content[: m.start()] + new_content[m.end() :]
                target.write_text(new_content, encoding="utf-8")
                return True

        return True

    def _repair_undefined_resource(
        self,
        build_workspace: Path,
        primary_diagnostic: CompilerDiagnostic | None,
        all_diagnostics: list[CompilerDiagnostic],
        raw_output: str = "",
    ) -> bool:
        """Inject missing resource definitions into res/values/strings.xml."""
        values_dir = build_workspace / "res" / "values"
        values_dir.mkdir(parents=True, exist_ok=True)
        strings_file = values_dir / "strings.xml"

        msg = primary_diagnostic.message if primary_diagnostic else ""
        res_match = re.search(
            r"['\"]?(?:string/)?([a-zA-Z0-9_]+)['\"]?\s+(?:not found|unresolved)",
            msg,
            re.IGNORECASE,
        )
        res_name = res_match.group(1) if res_match else "placeholder_resource"

        if strings_file.is_file():
            content = strings_file.read_text(encoding="utf-8", errors="replace")
            if f'name="{res_name}"' not in content:
                idx = content.rfind("</resources>")
                if idx >= 0:
                    injected = f'    <string name="{res_name}">{res_name}</string>\n'
                    new_content = content[:idx] + injected + content[idx:]
                    strings_file.write_text(new_content, encoding="utf-8")
                    return True
        else:
            initial_xml = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                f'<resources>\n    <string name="{res_name}">{res_name}</string>\n</resources>\n'
            )
            strings_file.write_text(initial_xml, encoding="utf-8")
            return True

        return True

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
