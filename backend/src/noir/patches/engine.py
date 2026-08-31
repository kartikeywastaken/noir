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

    def validate_patch(self, patch: PatchSet) -> list[str]:
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

        return errors

    def _prepare(self, patch):
        """Stage all operations and reject ambiguous/no-op changes before touching the workspace."""
        if not patch.operations:
            raise PatchValidationError("Patch contains no operations")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", patch.patch_id):
            raise PatchValidationError("Invalid patch identifier")
        self.workspace.changes_dir.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=self.workspace.changes_dir, prefix="stage-")
        stage = Path(temporary.name)
        staged = PatchEngine(
            SimpleNamespace(decoded_dir=stage, changes_dir=self.workspace.changes_dir)
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
                    if target.exists() and (
                        not target.is_file() or target.stat().st_size > 1_000_000
                    ):
                        raise PatchValidationError("Only bounded text files can be patched")
                    original = target.read_bytes() if target.exists() else None
                    if original is not None:
                        original.decode("utf-8")
                    before[op.relative_path] = original
                    if original is not None:
                        staged_target = safe_resolve(stage, op.relative_path)
                        staged_target.parent.mkdir(parents=True, exist_ok=True)
                        staged_target.write_bytes(original)
                if op.expected_absent and target.exists():
                    raise PatchValidationError("Expected absent file already exists")
                if op.expected_preimage_hash and (
                    not target.exists() or compute_file_hash(target) != op.expected_preimage_hash
                ):
                    raise PatchValidationError("Preimage hash mismatch")
                staged_op = op.model_copy(
                    update={"expected_preimage_hash": None, "expected_absent": False}
                )
                single = patch.model_copy(update={"operations": [staged_op]})
                errors = staged.validate_patch(single)
                if errors:
                    raise PatchValidationError("\n".join(errors))
                if op.operation == PatchOperationType.REPLACE_BLOCK and not op.match_content:
                    raise PatchValidationError("Replacement requires nonempty exact match")
                staged._apply_operation(op, safe_resolve(stage, op.relative_path))
            for relative, original in before.items():
                target = safe_resolve(stage, relative)
                new = target.read_bytes() if target.exists() else None
                if new == original:
                    raise PatchValidationError(f"Operation produces no change: {relative}")
            return temporary, stage, before
        except BaseException:
            temporary.cleanup()
            raise

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

        return diff

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
