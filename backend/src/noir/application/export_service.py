"""Export service — exports signed APKs and audit reports."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from noir.auditing.reporter import AuditReporter
from noir.domain.config import NoirConfig, get_config
from noir.infrastructure.database.repositories import BuildRepository, ProjectRepository
from noir.infrastructure.filesystem.workspace import ProjectWorkspace, compute_file_hash


class ExportServiceError(Exception):
    pass


class ExportService:
    """Exports project artifacts to a specified directory."""

    def __init__(self, config: NoirConfig | None = None):
        self.config = config or get_config()
        self.project_repo = ProjectRepository()
        self.build_repo = BuildRepository()
        self.reporter = AuditReporter(config)

    def export(
        self,
        project_id: str,
        build_id: str,
        output_dir: str,
        *,
        overwrite: bool = False,
        include_patches: bool = False,
    ) -> dict:
        """Export signed APK and reports to output directory.

        Args:
            project_id: Project ID.
            build_id: Build ID to export.
            output_dir: Destination directory.
            overwrite: Allow overwriting existing files.
            include_patches: Include patch/diff bundle.
        """
        project = self.project_repo.get(project_id)
        if not project:
            raise ExportServiceError(f"Project not found: {project_id}")

        build = self.build_repo.get(build_id)
        if not build or build.project_id != project_id:
            raise ExportServiceError(f"Build not found: {build_id}")

        if not build.success:
            raise ExportServiceError("Cannot export a failed build")
        package = re.sub(r"[^A-Za-z0-9._-]", "_", project.package_name or "app")
        version = re.sub(r"[^A-Za-z0-9._-]", "_", project.version_name or "modified")
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        exported: dict = {"files": []}

        # Export signed APK
        if build.signed_apk_path and Path(build.signed_apk_path).exists():
            apk_name = f"{package}-{version}-signed.apk"
            dest = out / apk_name
            if dest.exists() and not overwrite:
                raise ExportServiceError(
                    f"File already exists: {dest}. Use --overwrite to replace."
                )
            if compute_file_hash(Path(build.signed_apk_path)) != build.signed_apk_hash:
                raise ExportServiceError("Signed artifact was altered after verification")
            shutil.copy2(build.signed_apk_path, dest)

            # Verify hash
            actual_hash = compute_file_hash(dest)
            expected_hash = build.signed_apk_hash
            if expected_hash and actual_hash != expected_hash:
                raise ExportServiceError(
                    f"Exported APK hash mismatch! Expected {expected_hash}, got {actual_hash}"
                )

            exported["files"].append(
                {
                    "type": "signed_apk",
                    "path": str(dest),
                    "sha256": actual_hash,
                }
            )
        elif build.unsigned_apk_path and Path(build.unsigned_apk_path).exists():
            apk_name = f"{package}-unsigned.apk"
            dest = out / apk_name
            if dest.exists() and not overwrite:
                raise ExportServiceError(f"File already exists: {dest}")
            shutil.copy2(build.unsigned_apk_path, dest)
            exported["files"].append(
                {
                    "type": "unsigned_apk",
                    "path": str(dest),
                    "sha256": compute_file_hash(dest),
                }
            )

        # Generate and export reports
        workspace = ProjectWorkspace(project_id, self.config)
        report_paths = self.reporter.save_reports(project_id)

        for fmt, src_path in report_paths.items():
            src = Path(src_path)
            if src.exists():
                dest = out / src.name
                if dest.exists() and not overwrite:
                    continue
                shutil.copy2(src, dest)
                exported["files"].append(
                    {
                        "type": f"report_{fmt}",
                        "path": str(dest),
                    }
                )

        # Optionally export patches
        if include_patches:
            changes_dir = workspace.changes_dir
            if changes_dir.exists():
                patches_dest = out / "patches"
                if patches_dest.exists() and overwrite:
                    shutil.rmtree(patches_dest)
                if not patches_dest.exists():
                    shutil.copytree(changes_dir, patches_dest)
                    exported["files"].append(
                        {
                            "type": "patches",
                            "path": str(patches_dest),
                        }
                    )

        exported["project_id"] = project_id
        exported["build_id"] = build_id
        exported["output_dir"] = str(out)

        return exported
