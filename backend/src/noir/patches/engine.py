"""Safe Patch Engine.

Applies deterministic, transactional patches to decoded APK workspaces.
Supports text file operations, manifest XML edits, and Smali modifications.
Uses a transaction journal for crash-safe application.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

from noir.domain.config import get_config
from noir.domain.enums import PatchOperationType
from noir.domain.models import PatchOperation, PatchSet
from noir.infrastructure.filesystem.workspace import (
    PathSecurityError,
    ProjectWorkspace,
    compute_content_hash,
    compute_file_hash,
    safe_resolve,
)
from noir.security.xml import fromstring, parse

ANDROID_NS = "http://schemas.android.com/apk/res/android"

BINARY_OPERATIONS = {
    PatchOperationType.CIL_REPLACE_METHOD_BODY,
    PatchOperationType.CIL_INSERT_METHOD,
    PatchOperationType.CIL_REPLACE_FIELD_INIT,
    PatchOperationType.IL2CPP_FORCE_RETURN,
    PatchOperationType.IL2CPP_NOP_RANGE,
    PatchOperationType.NATIVE_BYTE_PATCH,
    PatchOperationType.NATIVE_NOP_RANGE,
    PatchOperationType.NATIVE_BRANCH_REDIRECT,
}
CIL_OPERATIONS = {
    PatchOperationType.CIL_REPLACE_METHOD_BODY,
    PatchOperationType.CIL_INSERT_METHOD,
    PatchOperationType.CIL_REPLACE_FIELD_INIT,
}
IL2CPP_OPERATIONS = {
    PatchOperationType.IL2CPP_FORCE_RETURN,
    PatchOperationType.IL2CPP_NOP_RANGE,
}
NATIVE_OPERATIONS = {
    PatchOperationType.NATIVE_BYTE_PATCH,
    PatchOperationType.NATIVE_NOP_RANGE,
    PatchOperationType.NATIVE_BRANCH_REDIRECT,
}


class PatchError(Exception):
    """Raised when patch operations fail."""

    pass


class PatchValidationError(PatchError):
    """Raised when patch validation fails before application."""

    pass


class PatchEngine:
    """Safe deterministic patch engine with transactional application."""

    def __init__(self, workspace: ProjectWorkspace | SimpleNamespace):
        self.workspace = workspace
        self.decoded_dir = workspace.decoded_dir
        self.journal_dir = workspace.changes_dir / "journal"
        self.config = getattr(workspace, "config", None) or get_config()

    def validate_patch(self, patch: PatchSet, *, check_multi_abi: bool = True) -> list[str]:
        """Validate all operations without applying.

        Returns list of validation errors (empty = valid).
        """
        errors: list[str] = []

        for i, op in enumerate(patch.operations):
            prefix = f"Operation {i} ({op.operation.value} {op.relative_path})"

            # Path safety
            try:
                safe_resolve(self.decoded_dir, op.relative_path)
            except PathSecurityError as e:
                errors.append(f"{prefix}: {e}")
                continue

            target = self.decoded_dir / op.relative_path

            if op.operation == PatchOperationType.CREATE_FILE:
                if target.exists():
                    errors.append(f"{prefix}: File already exists")

            elif op.operation == PatchOperationType.REPLACE_FILE:
                if not target.exists():
                    errors.append(f"{prefix}: File does not exist")
                elif op.expected_preimage_hash:
                    actual = compute_file_hash(target)
                    if actual != op.expected_preimage_hash:
                        errors.append(
                            f"{prefix}: Preimage hash mismatch "
                            f"(expected {op.expected_preimage_hash[:16]}..., "
                            f"got {actual[:16]}...)"
                        )

            elif op.operation == PatchOperationType.REPLACE_BLOCK:
                if not target.exists():
                    errors.append(f"{prefix}: File does not exist")
                elif op.match_content:
                    try:
                        content = target.read_text(errors="replace")
                        count = content.count(op.match_content)
                        if count == 0:
                            errors.append(f"{prefix}: Match content not found")
                        elif count > 1:
                            errors.append(
                                f"{prefix}: Match content is ambiguous "
                                f"({count} occurrences found, expected exactly 1)"
                            )
                    except OSError as e:
                        errors.append(f"{prefix}: Cannot read file: {e}")

            elif op.operation == PatchOperationType.DELETE_FILE:
                if not target.exists():
                    errors.append(f"{prefix}: File does not exist")
                elif op.expected_preimage_hash:
                    actual = compute_file_hash(target)
                    if actual != op.expected_preimage_hash:
                        errors.append(f"{prefix}: Preimage hash mismatch")

            elif op.operation in (
                PatchOperationType.MANIFEST_ADD,
                PatchOperationType.MANIFEST_UPDATE,
                PatchOperationType.MANIFEST_REMOVE,
            ):
                manifest = self.decoded_dir / "AndroidManifest.xml"
                if not manifest.exists():
                    errors.append(f"{prefix}: AndroidManifest.xml not found")

            elif op.operation == PatchOperationType.SMALI_REPLACE_METHOD:
                if not op.class_descriptor or not op.method_signature:
                    errors.append(f"{prefix}: Missing class_descriptor or method_signature")
                elif not target.exists():
                    errors.append(f"{prefix}: Smali file does not exist")
                else:
                    try:
                        content = target.read_text(errors="replace")
                        if not re.search(
                            r"(?m)^\.class[^\n]*\s" + re.escape(op.class_descriptor) + r"\s*$",
                            content,
                        ):
                            errors.append(f"{prefix}: Class descriptor not found in file")
                        if op.method_signature and op.method_signature not in content:
                            errors.append(f"{prefix}: Method signature not found in file")
                    except OSError as e:
                        errors.append(f"{prefix}: Cannot read file: {e}")

            elif op.operation == PatchOperationType.SMALI_INSERT_AT_ANCHOR:
                if not op.anchor:
                    errors.append(f"{prefix}: Missing anchor for insertion")
                elif not target.exists():
                    errors.append(f"{prefix}: Smali file does not exist")
                else:
                    try:
                        content = target.read_text(errors="replace")
                        if op.anchor not in content:
                            errors.append(f"{prefix}: Anchor not found in file")
                        count = content.count(op.anchor)
                        if count > 1:
                            errors.append(f"{prefix}: Anchor is ambiguous ({count} occurrences)")
                    except OSError as e:
                        errors.append(f"{prefix}: Cannot read file: {e}")

            elif op.operation in CIL_OPERATIONS:
                errors.extend(self._validate_cil_operation(op, target, prefix))

            elif op.operation in IL2CPP_OPERATIONS:
                errors.extend(self._validate_il2cpp_operation(op, target, prefix))

            elif op.operation in NATIVE_OPERATIONS:
                errors.extend(self._validate_native_operation(op, target, prefix))

        if check_multi_abi:
            errors.extend(self._validate_multi_abi(patch))

        return errors

    def _validate_binary_file(
        self, op: PatchOperation, target: Path, prefix: str, maximum: int
    ) -> list[str]:
        errors = []
        if not target.is_file():
            errors.append(f"{prefix}: Binary target does not exist")
            return errors
        if target.is_symlink():
            errors.append(f"{prefix}: Binary target cannot be a symlink")
        if target.stat().st_size > maximum:
            errors.append(f"{prefix}: Binary target exceeds its {maximum:,}-byte format ceiling")
        if not op.expected_preimage_hash:
            errors.append(f"{prefix}: Whole-file preimage hash is mandatory")
        elif compute_file_hash(target) != op.expected_preimage_hash:
            errors.append(f"{prefix}: Whole-file preimage hash mismatch")
        return errors

    def _validate_cil_operation(self, op: PatchOperation, target: Path, prefix: str) -> list[str]:
        errors = self._validate_binary_file(op, target, prefix, self.config.max_assembly_size)
        if errors:
            return errors
        if target.suffix.lower() != ".dll":
            return [f"{prefix}: CIL operations require a .dll assembly"]
        parts = Path(op.relative_path).parts
        if len(parts) != 5 or tuple(part.lower() for part in parts[:4]) != (
            "assets",
            "bin",
            "data",
            "managed",
        ):
            return [f"{prefix}: CIL operations are limited to assets/bin/Data/Managed/*.dll"]
        if not op.assembly_name:
            errors.append(f"{prefix}: assembly_name is required")
        elif op.assembly_name != target.name:
            errors.append(f"{prefix}: assembly_name does not match relative_path")
        if not op.type_full_name:
            errors.append(f"{prefix}: type_full_name is required")
        if op.operation == PatchOperationType.CIL_REPLACE_FIELD_INIT:
            if not op.field_name or op.new_il_source is None:
                errors.append(f"{prefix}: field_name and new_il_source are required")
            if not op.expected_method_il_hash:
                errors.append(f"{prefix}: Field initializer preimage hash is mandatory")
        else:
            if not op.method_signature or not op.new_il_source:
                errors.append(f"{prefix}: method_signature and new_il_source are required")
            if (
                op.operation == PatchOperationType.CIL_REPLACE_METHOD_BODY
                and not op.expected_method_il_hash
            ):
                errors.append(f"{prefix}: Method CIL preimage hash is mandatory")
        if op.new_il_source and len(op.new_il_source.encode("utf-8")) > 64 * 1024:
            errors.append(f"{prefix}: CIL source exceeds the 64 KiB operation ceiling")
        if errors:
            return errors
        try:
            from noir.infrastructure.dotnet.adapter import inspect_assembly

            inspection = inspect_assembly(self.config, target)
            types = [
                item
                for item in inspection.get("types", [])
                if item.get("full_name") == op.type_full_name
            ]
            if len(types) != 1:
                errors.append(f"{prefix}: Type selector matched {len(types)} types")
                return errors
            selected = types[0]
            if op.operation == PatchOperationType.CIL_REPLACE_FIELD_INIT:
                fields = [
                    field
                    for field in selected.get("fields", [])
                    if field.get("name") == op.field_name
                ]
                if len(fields) != 1:
                    errors.append(f"{prefix}: Field selector matched {len(fields)} fields")
                elif not fields[0].get("is_literal"):
                    errors.append(f"{prefix}: Only literal field initializers are supported")
                elif fields[0].get("constant_hash") != op.expected_method_il_hash:
                    errors.append(f"{prefix}: Field initializer preimage hash mismatch")
            else:
                methods = [
                    method
                    for method in selected.get("methods", [])
                    if method.get("signature") == op.method_signature
                ]
                expected_count = 0 if op.operation == PatchOperationType.CIL_INSERT_METHOD else 1
                if len(methods) != expected_count:
                    errors.append(
                        f"{prefix}: Method selector matched {len(methods)} methods; "
                        f"expected {expected_count}"
                    )
                elif methods and methods[0].get("il_hash") != op.expected_method_il_hash:
                    errors.append(f"{prefix}: Method CIL preimage hash mismatch")
        except Exception as exc:
            errors.append(f"{prefix}: {exc}")
        return errors

    def _native_range(self, op: PatchOperation, target: Path) -> tuple[int, int, bytes]:
        if op.native_offset is None or op.native_length is None:
            raise PatchValidationError("native_offset and native_length are required")
        if op.native_offset < 0 or op.native_length <= 0:
            raise PatchValidationError("Native patch range must be positive")
        data = target.read_bytes()
        if op.native_offset + op.native_length > len(data):
            raise PatchValidationError("Native patch range is outside the file")
        return (
            op.native_offset,
            op.native_length,
            data[op.native_offset : op.native_offset + op.native_length],
        )

    def _validate_native_operation(
        self, op: PatchOperation, target: Path, prefix: str
    ) -> list[str]:
        errors = self._validate_binary_file(op, target, prefix, self.config.max_native_library_size)
        if errors:
            return errors
        if target.suffix.lower() != ".so":
            return [f"{prefix}: Native operations require an ELF .so library"]
        parts = Path(op.relative_path).parts
        if (
            len(parts) != 3
            or parts[0] != "lib"
            or parts[1] != op.native_abi
            or not parts[2].endswith(".so")
        ):
            return [f"{prefix}: Native target must be lib/<declared-abi>/<library>.so"]
        if op.native_abi not in {"arm64-v8a", "armeabi-v7a", "x86", "x86_64"}:
            errors.append(f"{prefix}: A supported native_abi is required")
            return errors
        try:
            from noir.infrastructure.native.adapter import (
                disassemble_range,
                inspect_elf,
                range_hash,
            )

            inspection = inspect_elf(target)
            if inspection["abi"] != op.native_abi:
                errors.append(f"{prefix}: Declared ABI does not match the ELF header")
                return errors
            offset, length, _ = self._native_range(op, target)
            if not op.expected_native_bytes_hash:
                errors.append(f"{prefix}: Native range preimage hash is mandatory")
            elif range_hash(target, offset, length) != op.expected_native_bytes_hash:
                errors.append(f"{prefix}: Native range preimage hash mismatch")
            disassemble_range(target, offset, length, abi=op.native_abi)
            if op.operation == PatchOperationType.NATIVE_BYTE_PATCH:
                try:
                    replacement = bytes.fromhex(op.native_new_bytes_hex or "")
                except ValueError:
                    replacement = b""
                if len(replacement) != length:
                    errors.append(f"{prefix}: native_new_bytes_hex must be exactly {length} bytes")
            elif op.operation == PatchOperationType.NATIVE_BRANCH_REDIRECT:
                if op.native_redirect_target_offset is None:
                    errors.append(f"{prefix}: native_redirect_target_offset is required")
                elif op.native_redirect_target_offset not in {
                    symbol["file_offset"] for symbol in inspection["symbol_details"]
                }:
                    errors.append(f"{prefix}: Redirect target must be an exported function start")
        except Exception as exc:
            errors.append(f"{prefix}: {exc}")
        return errors

    def _metadata_path(self) -> Path:
        matches: list[Path] = sorted(Path(self.decoded_dir).rglob("global-metadata.dat"))
        if len(matches) != 1:
            raise PatchValidationError(
                f"Expected exactly one global-metadata.dat, found {len(matches)}"
            )
        metadata = matches[0]
        if metadata.stat().st_size > self.config.max_il2cpp_metadata_size:
            raise PatchValidationError("IL2CPP metadata exceeds the configured ceiling")
        return metadata

    def _resolve_il2cpp(self, op: PatchOperation, target: Path):
        from noir.infrastructure.il2cpp.metadata import Il2CppMetadata

        if not op.il2cpp_type_full_name or not op.il2cpp_method_signature:
            raise PatchValidationError(
                "il2cpp_type_full_name and il2cpp_method_signature are required"
            )
        return Il2CppMetadata(self._metadata_path(), target).find_method(
            op.il2cpp_type_full_name, op.il2cpp_method_signature
        )

    def _validate_il2cpp_operation(
        self, op: PatchOperation, target: Path, prefix: str
    ) -> list[str]:
        errors = self._validate_binary_file(op, target, prefix, self.config.max_native_library_size)
        if errors:
            return errors
        if target.name != "libil2cpp.so":
            return [f"{prefix}: IL2CPP operations require libil2cpp.so"]
        if op.native_abi not in {"arm64-v8a", "armeabi-v7a", "x86", "x86_64"}:
            return [f"{prefix}: A supported native_abi is required"]
        if Path(op.relative_path).parts != ("lib", op.native_abi, "libil2cpp.so"):
            return [f"{prefix}: IL2CPP target must be lib/<declared-abi>/libil2cpp.so"]
        try:
            from noir.infrastructure.native.adapter import disassemble_range, range_hash

            reference = self._resolve_il2cpp(op, target)
            if op.native_abi != reference.abi:
                errors.append(f"{prefix}: Declared ABI does not match the IL2CPP binary")
            offset = reference.file_offset if op.native_offset is None else op.native_offset
            length = op.native_length or reference.size
            if (
                offset < reference.file_offset
                or offset + length > reference.file_offset + reference.size
            ):
                errors.append(f"{prefix}: Patch range escapes the resolved IL2CPP method")
                return errors
            if (
                op.operation == PatchOperationType.IL2CPP_FORCE_RETURN
                and offset != reference.file_offset
            ):
                errors.append(f"{prefix}: Force-return patch must start at the function entry")
            if op.operation == PatchOperationType.IL2CPP_NOP_RANGE and (
                op.native_offset is None or op.native_length is None
            ):
                errors.append(f"{prefix}: IL2CPP NOP requires an explicit bounded range")
            if not op.expected_function_bytes_hash:
                errors.append(f"{prefix}: Function-byte preimage hash is mandatory")
            elif range_hash(target, offset, length) != op.expected_function_bytes_hash:
                errors.append(f"{prefix}: Function-byte preimage hash mismatch")
            disassemble_range(target, offset, length, abi=reference.abi)
            if (
                op.operation == PatchOperationType.IL2CPP_FORCE_RETURN
                and op.il2cpp_return_constant is None
            ):
                errors.append(f"{prefix}: il2cpp_return_constant is required")
        except Exception as exc:
            errors.append(f"{prefix}: {exc}")
        return errors

    def _validate_multi_abi(self, patch: PatchSet) -> list[str]:
        errors = []
        by_library: dict[str, list[PatchOperation]] = {}
        for op in patch.operations:
            if op.operation in NATIVE_OPERATIONS | IL2CPP_OPERATIONS:
                by_library.setdefault(Path(op.relative_path).name, []).append(op)
        for library, operations in by_library.items():
            present = {
                path.parent.name
                for path in self.decoded_dir.glob(f"lib/*/{library}")
                if path.is_file()
            }
            patched = {op.native_abi or Path(op.relative_path).parent.name for op in operations}
            skipped = {abi for op in operations for abi in op.native_skipped_abis}
            if skipped and not all(
                op.native_skip_reason and op.native_skip_reason.strip()
                for op in operations
                if op.native_skipped_abis
            ):
                errors.append(
                    f"Native library {library} has skipped ABIs without an explicit reason"
                )
            missing = present - patched - skipped
            if missing:
                errors.append(
                    f"Native library {library} exists for unaddressed ABIs: "
                    f"{', '.join(sorted(missing))}"
                )
            unknown = skipped - present
            if unknown:
                errors.append(
                    f"Native library {library} declares nonexistent skipped ABIs: "
                    f"{', '.join(sorted(unknown))}"
                )
        return errors

    def _prepare(self, patch):
        """Stage all operations and reject ambiguous/no-op changes before touching the workspace."""
        if not patch.operations:
            raise PatchValidationError("Patch contains no operations")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", patch.patch_id):
            raise PatchValidationError("Invalid patch identifier")
        validation_errors = self.validate_patch(patch)
        if validation_errors:
            raise PatchValidationError("\n".join(validation_errors))
        self.workspace.changes_dir.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=self.workspace.changes_dir, prefix="stage-")
        stage = Path(temporary.name)
        staged = PatchEngine(
            SimpleNamespace(
                decoded_dir=stage,
                changes_dir=self.workspace.changes_dir,
                config=self.config,
            )
        )
        before = {}
        try:
            for op in patch.operations:
                target = safe_resolve(self.decoded_dir, op.relative_path)
                if target == self.decoded_dir or target.is_symlink():
                    raise PatchValidationError("Invalid patch file")
                if (
                    op.operation.value.startswith("manifest_")
                    and op.relative_path != "AndroidManifest.xml"
                ):
                    raise PatchValidationError(
                        "Manifest operations must target AndroidManifest.xml"
                    )
                if op.relative_path not in before:
                    if target.exists() and not target.is_file():
                        raise PatchValidationError("Only regular files can be patched")
                    if (
                        target.exists()
                        and op.operation not in BINARY_OPERATIONS
                        and target.stat().st_size > 1_000_000
                    ):
                        raise PatchValidationError("Only bounded text files can be patched")
                    original = target.read_bytes() if target.exists() else None
                    if original is not None and op.operation not in BINARY_OPERATIONS:
                        original.decode("utf-8")
                    before[op.relative_path] = original
                    if original is not None:
                        staged_target = safe_resolve(stage, op.relative_path)
                        staged_target.parent.mkdir(parents=True, exist_ok=True)
                        staged_target.write_bytes(original)
                    if op.operation in IL2CPP_OPERATIONS:
                        metadata = self._metadata_path()
                        metadata_relative = metadata.relative_to(self.decoded_dir).as_posix()
                        staged_metadata = safe_resolve(stage, metadata_relative)
                        staged_metadata.parent.mkdir(parents=True, exist_ok=True)
                        staged_metadata.write_bytes(metadata.read_bytes())
                if op.expected_absent and target.exists():
                    raise PatchValidationError("Expected absent file already exists")
                if op.expected_preimage_hash and (
                    not target.exists() or compute_file_hash(target) != op.expected_preimage_hash
                ):
                    raise PatchValidationError("Preimage hash mismatch")
                staged_target = safe_resolve(stage, op.relative_path)
                staged_op = op.model_copy(
                    update={
                        "expected_preimage_hash": (
                            compute_file_hash(staged_target)
                            if op.operation in BINARY_OPERATIONS and staged_target.exists()
                            else None
                        ),
                        "expected_absent": False,
                    }
                )
                staged_op = self._bind_staged_cil_preimage(staged_op, staged_target)
                single = patch.model_copy(update={"operations": [staged_op]})
                # The complete patch was already checked against every packaged ABI.
                # A one-operation staging workspace intentionally contains only the
                # files copied so far, so repeating the cross-ABI check here would
                # reject an otherwise complete multi-ABI patch based on loop order.
                errors = staged.validate_patch(single, check_multi_abi=False)
                if errors:
                    raise PatchValidationError("\n".join(errors))
                if op.operation == PatchOperationType.REPLACE_BLOCK and not op.match_content:
                    raise PatchValidationError("Replacement requires nonempty exact match")
                staged._apply_operation(staged_op, staged_target)
            for relative, original in before.items():
                target = safe_resolve(stage, relative)
                new = target.read_bytes() if target.exists() else None
                if new == original:
                    raise PatchValidationError(f"Operation produces no change: {relative}")
            return temporary, stage, before
        except BaseException:
            temporary.cleanup()
            raise

    def _bind_staged_cil_preimage(self, op, target):
        """Bind a chained CIL edit to the current, already-validated staged image.

        Rewriting a managed assembly can renumber metadata tokens. Canonical CIL
        hashes intentionally include those tokens, so a later operation against
        the same assembly must use the staged method hash rather than the hash
        from the original image. The complete patch has already been validated
        against every original preimage before staging begins.
        """
        if op.operation not in CIL_OPERATIONS or not target.is_file():
            return op
        if op.operation == PatchOperationType.CIL_INSERT_METHOD:
            return op

        from noir.infrastructure.dotnet.adapter import inspect_assembly

        inspection = inspect_assembly(self.config, target)
        types = [
            item
            for item in inspection.get("types", [])
            if item.get("full_name") == op.type_full_name
        ]
        if len(types) != 1:
            raise PatchValidationError(f"Staged CIL type selector matched {len(types)} types")
        if op.operation == PatchOperationType.CIL_REPLACE_FIELD_INIT:
            fields = [
                field for field in types[0].get("fields", []) if field.get("name") == op.field_name
            ]
            if len(fields) != 1 or not fields[0].get("constant_hash"):
                raise PatchValidationError(
                    f"Staged CIL field selector matched {len(fields)} fields"
                )
            return op.model_copy(update={"expected_method_il_hash": fields[0]["constant_hash"]})

        methods = [
            method
            for method in types[0].get("methods", [])
            if method.get("signature") == op.method_signature
        ]
        if len(methods) != 1 or not methods[0].get("il_hash"):
            raise PatchValidationError(f"Staged CIL method selector matched {len(methods)} methods")
        return op.model_copy(update={"expected_method_il_hash": methods[0]["il_hash"]})

    @staticmethod
    def _atomic_write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".noir-write-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)

    def _journal_write(self, path, journal):
        self._atomic_write(path, json.dumps(journal, indent=2).encode())

    def recover(self):
        """Restore preimages from any interrupted patch transaction."""
        if not self.journal_dir.exists():
            return
        for path in self.journal_dir.glob("journal_*.json"):
            journal = json.loads(path.read_text())
            if not isinstance(journal, dict):
                continue  # Legacy journal; undo explicitly rejects these.
            if journal.get("status") not in ("applying", "undoing"):
                continue
            self._restore(journal)
            journal["status"] = "rolled_back" if journal["status"] == "applying" else "undone"
            self._journal_write(path, journal)

    def _restore(self, journal):
        for entry in reversed(journal["files"]):
            target = safe_resolve(self.decoded_dir, entry["path"])
            current = compute_file_hash(target) if target.exists() else None
            if current not in (entry["before_hash"], entry["after_hash"]):
                raise PatchError("Recovery conflict: workspace changed outside the transaction")
            if entry["before_hash"] is None:
                target.unlink(missing_ok=True)
            else:
                backup = safe_resolve(self.journal_dir, entry["backup"])
                if compute_file_hash(backup) != entry["before_hash"]:
                    raise PatchError("Patch backup integrity check failed")
                self._atomic_write(target, backup.read_bytes())

    def apply_patch(self, patch: PatchSet, *, dry_run: bool = False) -> dict:
        self.recover()
        temporary, stage, before = self._prepare(patch)
        with temporary:
            if dry_run:
                return {"dry_run": True, "valid": True, "operations": len(patch.operations)}
            self.journal_dir.mkdir(parents=True, exist_ok=True)
            journal_path = self.journal_dir / f"journal_{patch.patch_id}.json"
            if journal_path.exists():
                raise PatchError("Patch has already been attempted; generate a fresh patch")
            backup_dir = self.journal_dir / f"backup_{patch.patch_id}"
            backup_dir.mkdir()
            binary_paths = {
                operation.relative_path
                for operation in patch.operations
                if operation.operation in BINARY_OPERATIONS
            }
            entries = []
            for index, (relative, original) in enumerate(before.items()):
                staged_path = safe_resolve(stage, relative)
                new = staged_path.read_bytes() if staged_path.exists() else None
                entry = {
                    "path": relative,
                    "before_hash": compute_content_hash(original) if original is not None else None,
                    "after_hash": compute_content_hash(new) if new is not None else None,
                }
                if original is not None:
                    backup = backup_dir / str(index)
                    self._atomic_write(backup, original)
                    entry["backup"] = str(backup.relative_to(self.journal_dir))
                if relative in binary_paths and new is not None:
                    after_backup = backup_dir / f"{index}.after"
                    self._atomic_write(after_backup, new)
                    entry["after_backup"] = str(after_backup.relative_to(self.journal_dir))
                entries.append(entry)
            journal = {
                "version": 2,
                "patch_hash": patch.compute_hash(),
                "status": "applying",
                "files": entries,
            }
            self._journal_write(journal_path, journal)
            try:
                for entry in entries:
                    target = safe_resolve(self.decoded_dir, entry["path"])
                    current = compute_file_hash(target) if target.exists() else None
                    if current != entry["before_hash"]:
                        raise PatchError("Workspace changed after staging")
                    staged_path = safe_resolve(stage, entry["path"])
                    if staged_path.exists():
                        self._atomic_write(target, staged_path.read_bytes())
                    else:
                        target.unlink()
                journal["status"] = "committed"
                self._journal_write(journal_path, journal)
            except BaseException:
                self._restore(journal)
                journal["status"] = "rolled_back"
                self._journal_write(journal_path, journal)
                raise
            return {
                "patch_id": patch.patch_id,
                "operations_applied": len(patch.operations),
                "diffs": entries,
            }

    def _apply_operation(self, op: PatchOperation, target: Path) -> dict:
        """Apply a single patch operation."""
        diff: dict = {
            "path": op.relative_path,
            "operation": op.operation.value,
        }

        if op.operation == PatchOperationType.CREATE_FILE:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(op.new_content or "")
            diff["action"] = "created"

        elif op.operation == PatchOperationType.REPLACE_FILE:
            old_content = target.read_text(errors="replace") if target.exists() else ""
            target.write_text(op.new_content or "")
            diff["action"] = "replaced"
            diff["before_hash"] = compute_content_hash(old_content)
            diff["after_hash"] = compute_content_hash(op.new_content or "")

        elif op.operation == PatchOperationType.REPLACE_BLOCK:
            content = target.read_text(errors="replace")
            if op.match_content and op.new_content is not None:
                new_content = content.replace(op.match_content, op.new_content, 1)
                target.write_text(new_content)
                diff["action"] = "block_replaced"

        elif op.operation == PatchOperationType.DELETE_FILE:
            target.unlink()
            diff["action"] = "deleted"

        elif op.operation in (
            PatchOperationType.MANIFEST_ADD,
            PatchOperationType.MANIFEST_UPDATE,
            PatchOperationType.MANIFEST_REMOVE,
        ):
            self._apply_manifest_operation(op)
            diff["action"] = f"manifest_{op.operation.value}"

        elif op.operation == PatchOperationType.SMALI_REPLACE_METHOD:
            self._apply_smali_replace_method(op, target)
            diff["action"] = "smali_method_replaced"

        elif op.operation == PatchOperationType.SMALI_INSERT_AT_ANCHOR:
            self._apply_smali_insert(op, target)
            diff["action"] = "smali_inserted"

        elif op.operation in (
            PatchOperationType.XML_RESOURCE_ADD,
            PatchOperationType.XML_RESOURCE_UPDATE,
            PatchOperationType.XML_RESOURCE_REMOVE,
        ):
            self._apply_xml_resource_operation(op, target)
            diff["action"] = f"xml_{op.operation.value}"

        elif op.operation in CIL_OPERATIONS:
            self._apply_cil_operation(op, target)
            diff["action"] = op.operation.value

        elif op.operation in IL2CPP_OPERATIONS:
            self._apply_il2cpp_operation(op, target)
            diff["action"] = op.operation.value

        elif op.operation in NATIVE_OPERATIONS:
            self._apply_native_operation(op, target)
            diff["action"] = op.operation.value

        return diff

    def _apply_cil_operation(self, op: PatchOperation, target: Path) -> None:
        from noir.infrastructure.dotnet.adapter import patch_assembly, verify_assembly

        output = target.with_name(f".{target.name}.noir-cil-output")
        output.unlink(missing_ok=True)
        try:
            patch_assembly(
                self.config,
                target,
                output,
                {
                    "operation": op.operation.value,
                    "type_full_name": op.type_full_name,
                    "method_signature": op.method_signature,
                    "new_il_source": op.new_il_source,
                    "expected_method_il_hash": op.expected_method_il_hash,
                    "field_name": op.field_name,
                },
            )
            verify_assembly(self.config, output)
            os.replace(output, target)
        finally:
            output.unlink(missing_ok=True)

    def _apply_native_operation(self, op: PatchOperation, target: Path) -> None:
        from noir.infrastructure.native.adapter import (
            apply_branch_redirect,
            apply_byte_patch,
            apply_nop_range,
            disassemble_range,
        )

        offset, length, current = self._native_range(op, target)
        if op.operation == PatchOperationType.NATIVE_BYTE_PATCH:
            apply_byte_patch(target, offset, current, bytes.fromhex(op.native_new_bytes_hex or ""))
        elif op.operation == PatchOperationType.NATIVE_NOP_RANGE:
            apply_nop_range(target, offset, length, current, op.native_abi or "")
        else:
            apply_branch_redirect(
                target,
                offset,
                op.native_redirect_target_offset or 0,
                current,
                op.native_abi or "",
            )
        disassemble_range(target, offset, length, abi=op.native_abi)

    def _apply_il2cpp_operation(self, op: PatchOperation, target: Path) -> None:
        from noir.infrastructure.native.adapter import (
            apply_byte_patch,
            apply_nop_range,
            assemble_force_return,
            disassemble_range,
        )

        reference = self._resolve_il2cpp(op, target)
        offset = reference.file_offset if op.native_offset is None else op.native_offset
        length = op.native_length or reference.size
        current = target.read_bytes()[offset : offset + length]
        if op.operation == PatchOperationType.IL2CPP_FORCE_RETURN:
            replacement = assemble_force_return(
                reference.abi,
                op.il2cpp_return_constant or 0,
                length,
                reference.virtual_address + (offset - reference.file_offset),
            )
            apply_byte_patch(target, offset, current, replacement)
        else:
            apply_nop_range(target, offset, length, current, reference.abi)
        disassemble_range(target, offset, length, abi=reference.abi)

    def _apply_manifest_operation(self, op: PatchOperation) -> None:
        """Apply a manifest XML operation."""
        manifest_path = self.decoded_dir / "AndroidManifest.xml"
        ET.register_namespace("android", ANDROID_NS)
        tree = parse(manifest_path)
        root = tree.getroot()

        if op.operation == PatchOperationType.MANIFEST_ADD:
            if op.xml_element and op.new_content:
                # Parse the new element
                new_elem = fromstring(op.new_content)
                # Find the parent
                parent = (
                    root.find("application")
                    if "activity" in op.xml_element
                    or "service" in op.xml_element
                    or "receiver" in op.xml_element
                    or "provider" in op.xml_element
                    else root
                )
                if parent is not None:
                    # Check for duplicate
                    name_attr = f"{{{ANDROID_NS}}}name"
                    existing = [
                        e
                        for e in parent
                        if e.tag == new_elem.tag and e.get(name_attr) == new_elem.get(name_attr)
                    ]
                    if existing:
                        raise PatchError(
                            f"Duplicate element: {op.xml_element} with name "
                            f"'{new_elem.get(name_attr)}'"
                        )
                    parent.append(new_elem)

        elif op.operation == PatchOperationType.MANIFEST_UPDATE:
            if op.xml_element and op.xml_attributes:
                target_elem = self._find_manifest_element(root, op)
                if target_elem is not None:
                    for attr_key, attr_val in op.xml_attributes.items():
                        if ":" in attr_key:
                            ns, local = attr_key.split(":", 1)
                            if ns == "android":
                                target_elem.set(f"{{{ANDROID_NS}}}{local}", attr_val)
                            else:
                                target_elem.set(attr_key, attr_val)
                        else:
                            target_elem.set(attr_key, attr_val)

        elif op.operation == PatchOperationType.MANIFEST_REMOVE and op.xml_element:
            target_elem = self._find_manifest_element(root, op)
            if target_elem is not None:
                parent = next((p for p in root.iter() if target_elem in list(p)), None)
                if parent is None:
                    raise PatchError("Cannot remove manifest root")
                parent.remove(target_elem)

        tree.write(manifest_path, encoding="utf-8", xml_declaration=True)

    def _find_manifest_element(self, root: ET.Element, op: PatchOperation) -> ET.Element | None:
        """Find a manifest element by type and name."""
        name_attr = f"{{{ANDROID_NS}}}name"
        target_name = op.xml_attributes.get("android:name", "") or op.xml_attributes.get("name", "")

        matches = [
            elem
            for elem in root.iter(op.xml_element or "")
            if not target_name
            or elem.get(name_attr) == target_name
            or elem.get("name") == target_name
        ]
        if len(matches) != 1:
            selector = f"{op.xml_element or '<missing>'}"
            if target_name:
                selector += f" android:name={target_name!r}"
            raise PatchError(
                f"Manifest selector {selector} matched {len(matches)} elements; "
                "exactly one is required"
            )
        return matches[0]

    def _apply_smali_replace_method(self, op: PatchOperation, target: Path) -> None:
        """Replace an entire Smali method."""
        content = target.read_text(errors="replace")
        lines = content.splitlines(keepends=True)

        method_sig = op.method_signature or ""
        method_start = -1
        method_end = -1

        for i, line in enumerate(lines):
            if line.strip().startswith(".method") and line.strip().split()[-1] == method_sig:
                method_start = i
            elif method_start >= 0 and line.strip() == ".end method":
                method_end = i
                break

        if method_start < 0:
            raise PatchError(f"Method not found: {method_sig}")
        if method_end < 0:
            raise PatchError(f"Method end not found for: {method_sig}")

        # Replace method body
        replacement = (op.new_content or "").rstrip("\n") + "\n"
        new_lines = lines[:method_start] + [replacement] + lines[method_end + 1 :]
        target.write_text("".join(new_lines))

    @staticmethod
    def _smali_method_spans(content: str) -> list[tuple[str, int, int, int]]:
        """Return signature/start/end-directive/end spans; reject unbalanced methods."""
        spans = []
        active = None
        directives = re.finditer(r"(?m)^[ \t]*\.(?:method\b|end[ \t]+method\b)[^\r\n]*", content)
        for directive in directives:
            line = directive.group().split("#", 1)[0].strip()
            if line.startswith(".method"):
                if active is not None:
                    raise PatchError("Nested Smali methods are not allowed")
                signature = line.split()[-1]
                if not re.fullmatch(r"[^\s()]+\([^\s()]*\)[^\s()]+", signature):
                    raise PatchError("Invalid Smali method signature")
                active = (signature, directive.start())
            else:
                if active is None:
                    raise PatchError("Unmatched Smali .end method")
                spans.append((*active, directive.start(), directive.end()))
                active = None
        if active is not None:
            raise PatchError("Smali method is missing .end method")
        return spans

    def _apply_smali_insert(self, op: PatchOperation, target: Path) -> None:
        """Insert instructions in one method, or one new method at a class comment."""
        content = target.read_text(errors="replace")

        if not op.anchor or not op.new_content:
            raise PatchError("Missing anchor or new_content for insertion")

        count = content.count(op.anchor)
        if count == 0:
            raise PatchError(f"Anchor not found: {op.anchor}")
        if count > 1:
            raise PatchError(f"Ambiguous anchor ({count} occurrences): {op.anchor}")

        if not op.method_signature or not op.class_descriptor:
            raise PatchError("Smali insertion requires an exact class and method")
        if not re.search(
            r"(?m)^\.class[^\n]*\s" + re.escape(op.class_descriptor) + r"\s*$", content
        ):
            raise PatchError("Requested class does not match Smali file")
        spans = self._smali_method_spans(content)
        matches = [span for span in spans if span[0] == op.method_signature]
        anchor_start = content.index(op.anchor)
        insert_at = anchor_start + len(op.anchor)
        addition = op.new_content.strip()
        added_methods = self._smali_method_spans(addition)
        if matches:
            if added_methods:
                raise PatchError("Cannot insert a method with an existing signature")
            if len(matches) != 1 or not (
                matches[0][1] <= anchor_start < insert_at <= matches[0][2]
            ):
                raise PatchError("Anchor is not inside the requested method")
        else:
            # The absent-method case is NOT unrestricted class-level text insertion.
            # Require exactly one complete, correctly named method and a comment
            # anchor outside every existing method/annotation, on its own line.
            if len(added_methods) != 1:
                raise PatchError("New Smali insertion requires exactly one complete named method")
            signature, start, _, end = added_methods[0]
            if (signature, start, end) != (op.method_signature, 0, len(addition)):
                raise PatchError("New Smali insertion requires exactly one complete named method")
            line_start = content.rfind("\n", 0, anchor_start) + 1
            line_end = content.find("\n", insert_at)
            if line_end < 0:
                line_end = len(content)
            if (
                not re.fullmatch(r"[ \t]*#[^\r\n]+", op.anchor)
                or content[line_start:anchor_start].strip()
                or content[insert_at:line_end].strip()
                or any(start < insert_at and anchor_start < end for _, start, _, end in spans)
            ):
                raise PatchError("New methods require a unique class-level comment anchor")
            annotation_depth = 0
            for directive in re.finditer(
                r"(?m)^[ \t]*\.(annotation\b|end[ \t]+annotation\b)", content[:insert_at]
            ):
                annotation_depth += 1 if directive.group(1) == "annotation" else -1
                if annotation_depth < 0:
                    raise PatchError("Unbalanced Smali annotations")
            if annotation_depth:
                raise PatchError("New method anchor cannot be inside an annotation")
        target.write_text(content[:insert_at] + "\n" + op.new_content + content[insert_at:])

    def _apply_xml_resource_operation(self, op: PatchOperation, target: Path) -> None:
        """Apply XML resource file operations."""
        if op.operation == PatchOperationType.XML_RESOURCE_ADD:
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(op.new_content or "")
            else:
                # Add element to existing XML
                tree = parse(target)
                root = tree.getroot()
                if op.new_content:
                    new_elem = fromstring(op.new_content)
                    root.append(new_elem)
                tree.write(target, encoding="utf-8", xml_declaration=True)

        elif op.operation == PatchOperationType.XML_RESOURCE_UPDATE:
            if op.new_content:
                target.write_text(op.new_content)

        elif op.operation == PatchOperationType.XML_RESOURCE_REMOVE and target.exists():
            target.unlink()

    def undo_patch(self, patch: PatchSet) -> dict:
        self.recover()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", patch.patch_id):
            raise PatchError("Invalid patch identifier")
        journal_path = self.journal_dir / f"journal_{patch.patch_id}.json"
        if not journal_path.exists():
            raise PatchError("No recoverable journal for this patch")
        journal = json.loads(journal_path.read_text())
        if not isinstance(journal, dict) or journal.get("status") != "committed":
            raise PatchError("Patch is not committed or its legacy backups are unavailable")
        if journal["patch_hash"] != patch.compute_hash():
            raise PatchError("Journal does not match this patch")
        for entry in journal["files"]:
            target = safe_resolve(self.decoded_dir, entry["path"])
            current = compute_file_hash(target) if target.exists() else None
            if current != entry["after_hash"]:
                raise PatchError(f"Cannot undo: subsequent edits to {entry['path']}")
        journal["status"] = "undoing"
        self._journal_write(journal_path, journal)
        self._restore(journal)
        journal["status"] = "undone"
        self._journal_write(journal_path, journal)
        return {"patch_id": patch.patch_id, "undone_operations": len(patch.operations)}

    def generate_diff(self, patch: PatchSet) -> list[dict]:
        temporary, stage, before = self._prepare(patch)
        with temporary:
            result = []
            for relative, original in before.items():
                target = safe_resolve(stage, relative)
                related = [op for op in patch.operations if op.relative_path == relative]
                if any(op.operation in BINARY_OPERATIONS for op in related):
                    updated_bytes = target.read_bytes() if target.exists() else b""
                    result.append(
                        {
                            "path": relative,
                            "preview": "Binary patch preview; inspect structured operation fields",
                            "before_hash": compute_content_hash(original or b""),
                            "after_hash": compute_content_hash(updated_bytes),
                            "operations": [
                                {
                                    "operation": op.operation.value,
                                    "affected_scope": op.affected_scope,
                                    "abi": op.native_abi,
                                    "offset": op.native_offset,
                                    "length": op.native_length,
                                    "method": op.method_signature or op.il2cpp_method_signature,
                                    "type": op.type_full_name or op.il2cpp_type_full_name,
                                }
                                for op in related
                            ],
                        }
                    )
                    continue
                updated = target.read_text() if target.exists() else ""
                previous = original.decode() if original is not None else ""
                diff = "".join(
                    difflib.unified_diff(
                        previous.splitlines(keepends=True),
                        updated.splitlines(keepends=True),
                        fromfile=f"a/{relative}",
                        tofile=f"b/{relative}",
                    )
                )
                result.append({"path": relative, "preview": diff})
            return result
