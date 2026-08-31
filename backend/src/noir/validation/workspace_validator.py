"""Workspace validation — layered checks after changes are applied."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from noir.domain.config import NoirConfig, get_config
from noir.domain.enums import ValidationSeverity
from noir.domain.models import ValidationFinding, ValidationResult
from noir.infrastructure.database.repositories import (
    PatchRepository,
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

        # Layers 5-7: format-specific checks for approved, applied binary operations.
        binary_operations = self._applied_binary_operations(project_id)
        findings.extend(self._check_dotnet_assemblies(workspace, binary_operations))
        findings.extend(self._check_il2cpp_patches(workspace, binary_operations))
        findings.extend(self._check_native_libraries(workspace, binary_operations))

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

    @staticmethod
    def _applied_binary_operations(project_id: str):
        from noir.patches.engine import BINARY_OPERATIONS

        repository = PatchRepository()
        return [
            (patch, operation)
            for patch in repository.list_by_project(project_id)
            if repository.is_applied(patch.patch_id)
            for operation in patch.operations
            if operation.operation in BINARY_OPERATIONS
        ]

    def _check_dotnet_assemblies(self, workspace, operations) -> list[ValidationFinding]:
        from noir.domain.enums import PatchOperationType

        cil = {
            PatchOperationType.CIL_REPLACE_METHOD_BODY,
            PatchOperationType.CIL_INSERT_METHOD,
            PatchOperationType.CIL_REPLACE_FIELD_INIT,
        }
        selected = [(patch, op) for patch, op in operations if op.operation in cil]
        from noir.infrastructure.dotnet.adapter import inspect_assembly, verify_assembly

        grouped: dict[tuple[str, str], list[Any]] = {}
        for patch, operation in selected:
            grouped.setdefault((patch.patch_id, operation.relative_path), []).append(operation)
        findings: list[ValidationFinding] = []
        verified_paths: set[str] = set()
        for (patch_id, relative_path), patch_operations in grouped.items():
            target = workspace.safe_path(relative_path)
            try:
                verification = verify_assembly(self.config, target)
                verified_paths.add(relative_path)
                self._verify_cil_scope(
                    workspace,
                    patch_id,
                    patch_operations,
                    target,
                    inspect_assembly,
                )
                findings.append(
                    ValidationFinding(
                        check_name="dotnet_assembly",
                        severity=ValidationSeverity.INFO,
                        message=(
                            f"Verified managed assembly {target.name}: "
                            f"{verification.get('types', 0)} types, "
                            f"{verification.get('methods', 0)} methods"
                        ),
                        file_path=relative_path,
                    )
                )
            except Exception as exc:
                findings.append(
                    ValidationFinding(
                        check_name="dotnet_assembly",
                        severity=ValidationSeverity.ERROR,
                        message=f"Managed assembly verification failed: {exc}",
                        file_path=relative_path,
                    )
                )
        for target in sorted(
            workspace.decoded_dir.glob("assets/bin/Data/Managed/*.dll")
        ):
            relative_path = target.relative_to(workspace.decoded_dir).as_posix()
            if relative_path in verified_paths:
                continue
            try:
                verification = verify_assembly(self.config, target)
                findings.append(
                    ValidationFinding(
                        check_name="dotnet_assembly",
                        severity=ValidationSeverity.INFO,
                        message=(
                            f"Verified managed assembly {target.name}: "
                            f"{verification.get('types', 0)} types, "
                            f"{verification.get('methods', 0)} methods"
                        ),
                        file_path=relative_path,
                    )
                )
            except Exception as exc:
                findings.append(
                    ValidationFinding(
                        check_name="dotnet_assembly",
                        severity=ValidationSeverity.ERROR,
                        message=f"Managed assembly verification failed: {exc}",
                        file_path=relative_path,
                    )
                )
        return findings

    @staticmethod
    def _journal_after_path(workspace, patch_id, relative_path):
        import json

        journal_root = workspace.changes_dir / "journal"
        data = json.loads((journal_root / f"journal_{patch_id}.json").read_text())
        entry = next(item for item in data["files"] if item["path"] == relative_path)
        if entry.get("after_backup"):
            return journal_root / entry["after_backup"]
        current = workspace.safe_path(relative_path)
        from noir.infrastructure.filesystem.workspace import compute_file_hash

        if not current.is_file() or compute_file_hash(current) != entry.get("after_hash"):
            raise ValueError("Historical binary patch has no verifiable postimage snapshot")
        return current

    def _verify_cil_scope(
        self, workspace, patch_id, operations, target, inspect_assembly
    ) -> None:
        import json

        journal = workspace.changes_dir / "journal" / f"journal_{patch_id}.json"
        data = json.loads(journal.read_text())
        relative_path = operations[0].relative_path
        entry = next(item for item in data["files"] if item["path"] == relative_path)
        before_path = workspace.changes_dir / "journal" / entry["backup"]
        before = inspect_assembly(self.config, before_path)
        after_path = self._journal_after_path(workspace, patch_id, relative_path)
        after = inspect_assembly(self.config, after_path)

        def method_map(inspection):
            return {
                (type_info["full_name"], method["signature"]): method.get("il_hash")
                for type_info in inspection.get("types", [])
                for method in type_info.get("methods", [])
            }

        before_methods, after_methods = method_map(before), method_map(after)
        changed_methods = {
            key
            for key in before_methods.keys() | after_methods.keys()
            if before_methods.get(key) != after_methods.get(key)
        }

        def field_map(inspection):
            return {
                (type_info["full_name"], field["name"]): field.get("constant_hash")
                for type_info in inspection.get("types", [])
                for field in type_info.get("fields", [])
            }

        before_fields, after_fields = field_map(before), field_map(after)
        changed_fields = {
            key
            for key in before_fields.keys() | after_fields.keys()
            if before_fields.get(key) != after_fields.get(key)
        }
        allowed_methods = {
            (operation.type_full_name, operation.method_signature)
            for operation in operations
            if operation.operation.value != "cil_replace_field_init"
        }
        allowed_fields = {
            (operation.type_full_name, operation.field_name)
            for operation in operations
            if operation.operation.value == "cil_replace_field_init"
        }
        if changed_methods - allowed_methods:
            raise ValueError(
                "Managed patch changed methods outside the approved scope: "
                + ", ".join(
                    f"{kind}::{method}"
                    for kind, method in sorted(changed_methods - allowed_methods)[:5]
                )
            )
        if changed_fields - allowed_fields:
            raise ValueError(
                "Managed patch changed fields outside the approved scope: "
                + ", ".join(
                    f"{kind}::{field}"
                    for kind, field in sorted(changed_fields - allowed_fields)[:5]
                )
            )
        missing_methods = allowed_methods - changed_methods
        missing_fields = allowed_fields - changed_fields
        if missing_methods or missing_fields:
            missing = sorted(missing_methods | missing_fields)
            raise ValueError(
                "Managed patch did not change every approved scope: "
                + ", ".join(f"{kind}::{member}" for kind, member in missing[:5])
            )

    def _check_il2cpp_patches(self, workspace, operations) -> list[ValidationFinding]:
        from noir.domain.enums import PatchOperationType

        selected = [
            (patch, op)
            for patch, op in operations
            if op.operation
            in {PatchOperationType.IL2CPP_FORCE_RETURN, PatchOperationType.IL2CPP_NOP_RANGE}
        ]
        if not selected:
            return []
        from noir.infrastructure.il2cpp.metadata import Il2CppMetadata
        from noir.infrastructure.native.adapter import disassemble_range

        findings = []
        metadata = sorted(workspace.decoded_dir.rglob("global-metadata.dat"))
        for patch, op in selected:
            try:
                if len(metadata) != 1:
                    raise ValueError("Expected exactly one global-metadata.dat")
                target = self._journal_after_path(
                    workspace, patch.patch_id, op.relative_path
                )
                reference = Il2CppMetadata(metadata[0], target).find_method(
                    op.il2cpp_type_full_name or "", op.il2cpp_method_signature or ""
                )
                offset = reference.file_offset if op.native_offset is None else op.native_offset
                length = op.native_length or reference.size
                disassemble_range(target, offset, length, abi=reference.abi)
                findings.append(
                    ValidationFinding(
                        check_name="il2cpp_patch",
                        severity=ValidationSeverity.INFO,
                        message=(
                            f"Verified IL2CPP {reference.symbol_name} for {reference.abi} "
                            f"using metadata version {reference.metadata_version}"
                        ),
                        file_path=op.relative_path,
                    )
                )
            except Exception as exc:
                findings.append(
                    ValidationFinding(
                        check_name="il2cpp_patch",
                        severity=ValidationSeverity.ERROR,
                        message=f"IL2CPP verification failed: {exc}",
                        file_path=op.relative_path,
                    )
                )
        return findings

    def _check_native_libraries(self, workspace, operations) -> list[ValidationFinding]:
        from noir.domain.enums import PatchOperationType

        native = {
            PatchOperationType.NATIVE_BYTE_PATCH,
            PatchOperationType.NATIVE_NOP_RANGE,
            PatchOperationType.NATIVE_BRANCH_REDIRECT,
        }
        selected = [(patch, op) for patch, op in operations if op.operation in native]
        if not selected:
            return []
        from noir.infrastructure.native.adapter import disassemble_range, verify_elf

        findings = []
        verified_current = set()
        for patch, op in selected:
            try:
                current = workspace.safe_path(op.relative_path)
                if op.relative_path not in verified_current:
                    verify_elf(current)
                    verified_current.add(op.relative_path)
                target = self._journal_after_path(
                    workspace, patch.patch_id, op.relative_path
                )
                details = verify_elf(target)
                disassemble_range(
                    target, op.native_offset or 0, op.native_length or 0, abi=op.native_abi
                )
                findings.append(
                    ValidationFinding(
                        check_name="native_library",
                        severity=ValidationSeverity.INFO,
                        message=f"Verified {details['abi']} ELF and patched instruction range",
                        file_path=op.relative_path,
                    )
                )
            except Exception as exc:
                findings.append(
                    ValidationFinding(
                        check_name="native_library",
                        severity=ValidationSeverity.ERROR,
                        message=f"Native library verification failed: {exc}",
                        file_path=op.relative_path,
                    )
                )
        return findings

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
