"""Unit tests for in-place DEX string patching with lexical interval ordering
and PatchEngine integration.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from noir.domain.enums import PatchOperationType
from noir.domain.models import PatchOperation, PatchSet
from noir.infrastructure.dex.integrity import verify_dex_header
from noir.infrastructure.dex.patcher import (
    DexPatchError,
    DexStringOrderingError,
    patch_dex_bytes,
    patch_dex_file_string,
    patch_dex_string_in_place,
)
from noir.infrastructure.filesystem.workspace import compute_content_hash
from noir.patches.engine import PatchEngine

RUNTIME_DEX_PATH = Path(__file__).resolve().parents[2] / "runtime" / "dist" / "noir-runtime-v1.dex"


@pytest.fixture
def real_dex_bytes() -> bytes:
    assert RUNTIME_DEX_PATH.is_file(), f"Missing fixture DEX at {RUNTIME_DEX_PATH}"
    return RUNTIME_DEX_PATH.read_bytes()


def test_in_order_string_patch_succeeds(real_dex_bytes: bytes):
    """In-place string replacement preserving prev < new < next succeeds and updates header."""
    # In noir-runtime-v1.dex:
    # index 9: 'INSTALLED'
    # index 10: 'InitProvider.java' (17 bytes)
    # index 11: 'L'
    # 'InitProvider.test' is 17 bytes, and 'INSTALLED' < 'InitProvider.test' < 'L'
    old_str = "InitProvider.java"
    new_str = "InitProvider.test"

    patched = patch_dex_string_in_place(real_dex_bytes, old_str, new_str)
    assert new_str.encode("utf-8") in patched
    assert old_str.encode("utf-8") not in patched

    # Header must be recalculated and valid
    valid, msg = verify_dex_header(patched)
    assert valid is True, f"Patched DEX header failed verification: {msg}"
    assert msg == "OK"


def test_out_of_order_string_patch_new_le_prev_rejected(real_dex_bytes: bytes):
    """Replacement where new <= prev is rejected with DexStringOrderingError."""
    old_str = "InitProvider.java"
    # 'AaaaProvider.java' is 17 bytes, but sorts BEFORE 'INSTALLED' (index 9)
    new_str = "AaaaProvider.java"

    with pytest.raises(DexStringOrderingError) as exc_info:
        patch_dex_string_in_place(real_dex_bytes, old_str, new_str)

    err = str(exc_info.value)
    assert "Lexical ordering violation" in err
    assert "predecessor" in err


def test_out_of_order_string_patch_new_ge_next_rejected(real_dex_bytes: bytes):
    """Replacement where new >= next is rejected with DexStringOrderingError."""
    old_str = "InitProvider.java"
    # 'ZzzzProvider.java' is 17 bytes, but sorts AFTER 'L' (index 11)
    new_str = "ZzzzProvider.java"

    with pytest.raises(DexStringOrderingError) as exc_info:
        patch_dex_string_in_place(real_dex_bytes, old_str, new_str)

    err = str(exc_info.value)
    assert "Lexical ordering violation" in err
    assert "successor" in err


def test_unequal_byte_length_rejected(real_dex_bytes: bytes):
    """Different byte lengths violate equal length invariant and are rejected."""
    old_str = "InitProvider.java"
    new_str = "Short.java"  # 10 bytes vs 17 bytes

    with pytest.raises(ValueError) as exc_info:
        patch_dex_string_in_place(real_dex_bytes, old_str, new_str)

    assert "Byte length mismatch" in str(exc_info.value)


def test_nonexistent_string_rejected(real_dex_bytes: bytes):
    """String not in DEX string_ids is rejected."""
    with pytest.raises(DexPatchError) as exc_info:
        patch_dex_string_in_place(real_dex_bytes, "NonexistentString123", "ReplacementString123")

    assert "not found in DEX string_ids table" in str(exc_info.value)


def test_patch_dex_file_string_on_disk(tmp_path: Path, real_dex_bytes: bytes):
    """patch_dex_file_string updates DEX on disk."""
    dex_path = tmp_path / "classes.dex"
    dex_path.write_bytes(real_dex_bytes)

    out_path = tmp_path / "classes_patched.dex"
    patch_dex_file_string(dex_path, "InitProvider.java", "InitProvider.test", out_path)

    assert out_path.is_file()
    valid, msg = verify_dex_header(out_path.read_bytes())
    assert valid is True
    assert msg == "OK"


def test_patch_dex_bytes_rejects_verifier_violations(real_dex_bytes: bytes):
    """patch_dex_bytes rejects byte modification that introduces branch-into-move-result."""
    import struct

    # Attempt to patch 0xf94 with if-eqz targeting move-result at 0xf9e
    offset = 0xF94
    old_bytes = real_dex_bytes[offset : offset + 4]
    new_branch_bytes = struct.pack("<HH", 0x0038, 5)

    with pytest.raises(DexPatchError) as exc_info:
        patch_dex_bytes(
            real_dex_bytes,
            offset,
            old_bytes,
            new_branch_bytes,
            verify_bytecode=True,
        )

    assert "verifier" in str(exc_info.value).lower()


def test_patch_engine_dex_string_patch_integration(tmp_path: Path, real_dex_bytes: bytes):
    """PatchEngine validates and applies DEX_STRING_PATCH in workspace."""
    decoded_dir = tmp_path / "decoded"
    decoded_dir.mkdir()
    changes_dir = tmp_path / "changes"
    changes_dir.mkdir()

    dex_file = decoded_dir / "classes.dex"
    dex_file.write_bytes(real_dex_bytes)
    preimage_hash = compute_content_hash(real_dex_bytes)

    workspace = SimpleNamespace(
        decoded_dir=decoded_dir,
        changes_dir=changes_dir,
    )
    engine = PatchEngine(workspace)

    # 1. Valid in-order patch
    valid_op = PatchOperation(
        relative_path="classes.dex",
        operation=PatchOperationType.DEX_STRING_PATCH,
        expected_preimage_hash=preimage_hash,
        match_content="InitProvider.java",
        new_content="InitProvider.test",
    )
    patch = PatchSet(
        plan_id="plan_1",
        project_id="proj_1",
        workspace_revision=1,
        operations=[valid_op],
    )

    errors = engine.validate_patch(patch)
    assert len(errors) == 0, f"Unexpected validation errors: {errors}"

    result = engine.apply_patch(patch)
    assert result["operations_applied"] == 1

    # Verify result on disk
    patched_on_disk = dex_file.read_bytes()
    assert b"InitProvider.test" in patched_on_disk
    valid, msg = verify_dex_header(patched_on_disk)
    assert valid is True, f"Patched DEX on disk failed verification: {msg}"
    assert msg == "OK"


def test_patch_engine_dex_string_patch_ordering_rejection(tmp_path: Path, real_dex_bytes: bytes):
    """PatchEngine rejects DEX_STRING_PATCH when replacement violates lexical ordering."""
    decoded_dir = tmp_path / "decoded"
    decoded_dir.mkdir()
    changes_dir = tmp_path / "changes"
    changes_dir.mkdir()

    dex_file = decoded_dir / "classes.dex"
    dex_file.write_bytes(real_dex_bytes)

    workspace = SimpleNamespace(
        decoded_dir=decoded_dir,
        changes_dir=changes_dir,
    )
    engine = PatchEngine(workspace)

    invalid_op = PatchOperation(
        relative_path="classes.dex",
        operation=PatchOperationType.DEX_STRING_PATCH,
        match_content="InitProvider.java",
        new_content="AaaaProvider.java",  # out-of-order: new <= prev
    )
    patch = PatchSet(
        plan_id="plan_2",
        project_id="proj_2",
        workspace_revision=1,
        operations=[invalid_op],
    )

    errors = engine.validate_patch(patch)
    assert len(errors) >= 1
    assert any("ordering violation" in e.lower() for e in errors)
