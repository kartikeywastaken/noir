"""Workspace validation — layered checks after changes are applied."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from noir.domain.config import NoirConfig, get_config
from noir.domain.enums import ValidationSeverity
from noir.domain.models import ValidationFinding, ValidationResult
from noir.infrastructure.database.repositories import (
    ProjectRepository,
    ValidationRepository,
)
from noir.infrastructure.filesystem.workspace import (
    ProjectWorkspace,
)
from noir.security.xml import parse


class ValidationService:
    """Performs layered validation on decoded APK workspaces."""

    def __init__(self, config: NoirConfig | None = None):
        self.config = config or get_config()
        self.project_repo = ProjectRepository()
        self.validation_repo = ValidationRepository()

    def validate(self, project_id: str) -> ValidationResult:
        """Run all validation checks on a project workspace."""
        project = self.project_repo.get(project_id)
        if not project:
            raise ValueError(f"Project not found: {project_id}")

        workspace = ProjectWorkspace(project_id, self.config)
        findings: list[ValidationFinding] = []

        # Layer 1: Filesystem checks
        findings.extend(self._check_filesystem(workspace))

        # Layer 2: XML/resource checks
        findings.extend(self._check_xml_resources(workspace))

        # Layer 3: Manifest checks
        findings.extend(self._check_manifest(workspace))

        # Layer 4: Smali checks
        findings.extend(self._check_smali(workspace))

        # Build result
        error_count = sum(1 for f in findings if f.severity == ValidationSeverity.ERROR)
        warning_count = sum(1 for f in findings if f.severity == ValidationSeverity.WARNING)

        result = ValidationResult(
            project_id=project_id,
            workspace_revision=project.workspace_revision,
            findings=findings,
            passed=error_count == 0,
            error_count=error_count,
            warning_count=warning_count,
        )

        self.validation_repo.save(result)
        return result

    def _check_filesystem(self, workspace: ProjectWorkspace) -> list[ValidationFinding]:
        """Filesystem safety checks."""
        findings: list[ValidationFinding] = []
        decoded = workspace.decoded_dir

        if not decoded.exists():
            findings.append(
                ValidationFinding(
                    check_name="decoded_dir_exists",
                    severity=ValidationSeverity.ERROR,
                    message="Decoded workspace directory does not exist",
                )
            )
            return findings

        # Check for symlinks
        for path in decoded.rglob("*"):
            if path.is_symlink():
                try:
                    target = path.resolve()
                    decoded_resolved = decoded.resolve()
                    if not target.is_relative_to(decoded_resolved):
                        findings.append(
                            ValidationFinding(
                                check_name="symlink_escape",
                                severity=ValidationSeverity.ERROR,
                                message="Symlink escapes workspace boundary",
                                file_path=str(path.relative_to(decoded)),
                            )
                        )
                except OSError:
                    findings.append(
                        ValidationFinding(
                            check_name="broken_symlink",
                            severity=ValidationSeverity.WARNING,
                            message="Broken symlink detected",
                            file_path=str(path.relative_to(decoded)),
                        )
                    )

        # Check AndroidManifest.xml exists
        manifest = decoded / "AndroidManifest.xml"
        if not manifest.exists():
            findings.append(
                ValidationFinding(
                    check_name="manifest_exists",
                    severity=ValidationSeverity.ERROR,
                    message="AndroidManifest.xml not found in workspace",
                )
            )

        findings.append(
            ValidationFinding(
                check_name="filesystem_safety",
                severity=ValidationSeverity.INFO,
                message="Filesystem safety checks passed",
            )
        )

        return findings

    def _check_xml_resources(self, workspace: ProjectWorkspace) -> list[ValidationFinding]:
        """XML well-formedness checks."""
        findings: list[ValidationFinding] = []
        decoded = workspace.decoded_dir
        res_dir = decoded / "res"

        if not res_dir.exists():
            findings.append(
                ValidationFinding(
                    check_name="res_dir",
                    severity=ValidationSeverity.INFO,
                    message="No res/ directory (may be normal for some packages)",
                )
            )
            return findings

        xml_count = 0
        for xml_file in res_dir.rglob("*.xml"):
            xml_count += 1
            try:
                parse(xml_file)
            except ET.ParseError as e:
                findings.append(
                    ValidationFinding(
                        check_name="xml_wellformed",
                        severity=ValidationSeverity.ERROR,
                        message=f"Malformed XML: {e}",
                        file_path=str(xml_file.relative_to(decoded)),
                    )
                )

        findings.append(
            ValidationFinding(
                check_name="xml_resources",
                severity=ValidationSeverity.INFO,
                message=f"Checked {xml_count} XML resource files",
            )
        )

        return findings

    def _check_manifest(self, workspace: ProjectWorkspace) -> list[ValidationFinding]:
        """Manifest-specific checks."""
        findings: list[ValidationFinding] = []
        manifest_path = workspace.decoded_dir / "AndroidManifest.xml"

        if not manifest_path.exists():
            return findings

        try:
            tree = parse(manifest_path)
            root = tree.getroot()
        except ET.ParseError as e:
            findings.append(
                ValidationFinding(
                    check_name="manifest_parse",
                    severity=ValidationSeverity.ERROR,
                    message=f"Cannot parse AndroidManifest.xml: {e}",
                )
            )
            return findings

        android_ns = "http://schemas.android.com/apk/res/android"

        # Check package name
        package = root.get("package", "")
        if not package:
            findings.append(
                ValidationFinding(
                    check_name="manifest_package",
                    severity=ValidationSeverity.ERROR,
                    message="Missing package attribute in manifest",
                )
            )

        # Check component names
        app = root.find("application")
        if app is not None:
            for tag in ("activity", "service", "receiver", "provider"):
                for elem in app.findall(tag):
                    name = elem.get(f"{{{android_ns}}}name")
                    if not name:
                        findings.append(
                            ValidationFinding(
                                check_name="component_name",
                                severity=ValidationSeverity.WARNING,
                                message=f"Component {tag} missing android:name",
                            )
                        )

                    # Check exported attribute for components with intent filters
                    has_filters = len(elem.findall("intent-filter")) > 0
                    exported = elem.get(f"{{{android_ns}}}exported")
                    if has_filters and exported is None:
                        findings.append(
                            ValidationFinding(
                                check_name="exported_attribute",
                                severity=ValidationSeverity.WARNING,
                                message=(
                                    f"Component '{name or tag}' has intent-filters but "
                                    "no explicit android:exported attribute "
                                    "(required for targetSdk >= 31)"
                                ),
                                file_path="AndroidManifest.xml",
                            )
                        )

        findings.append(
            ValidationFinding(
                check_name="manifest_checks",
                severity=ValidationSeverity.INFO,
                message="Manifest validation completed",
            )
        )

        return findings

    def _check_smali(self, workspace: ProjectWorkspace) -> list[ValidationFinding]:
        """Basic Smali structure checks."""
        findings: list[ValidationFinding] = []
        decoded = workspace.decoded_dir

        smali_dirs = [d for d in decoded.iterdir() if d.is_dir() and d.name.startswith("smali")]
        if not smali_dirs:
            findings.append(
                ValidationFinding(
                    check_name="smali_dirs",
                    severity=ValidationSeverity.INFO,
                    message="No Smali directories found (may be resource-only package)",
                )
            )
            return findings

        # Check for duplicate class descriptors
        class_files: dict[str, list[str]] = {}
        for smali_dir in smali_dirs:
            for smali_file in smali_dir.rglob("*.smali"):
                try:
                    content = smali_file.read_text(errors="replace")
                    for line in content.splitlines():
                        if line.strip().startswith(".class"):
                            parts = line.strip().split()
                            descriptor = parts[-1] if parts else ""
                            if descriptor.startswith("L") and descriptor.endswith(";"):
                                rel = str(smali_file.relative_to(decoded))
                                if descriptor not in class_files:
                                    class_files[descriptor] = []
                                class_files[descriptor].append(rel)
                            break
                except OSError:
                    continue

        # Report duplicates
        for descriptor, files in class_files.items():
            if len(files) > 1:
                findings.append(
                    ValidationFinding(
                        check_name="duplicate_class",
                        severity=ValidationSeverity.ERROR,
                        message=f"Duplicate class descriptor: {descriptor}",
                        evidence=", ".join(files),
                    )
                )

        total_classes = len(class_files)
        findings.append(
            ValidationFinding(
                check_name="smali_structure",
                severity=ValidationSeverity.INFO,
                message=f"Checked {total_classes} Smali classes",
            )
        )

        return findings

    def get_latest(self, project_id: str) -> ValidationResult | None:
        """Get the latest validation result."""
        return self.validation_repo.get_latest(project_id)
