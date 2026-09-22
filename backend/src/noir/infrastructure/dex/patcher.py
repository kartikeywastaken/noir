"""In-Place DEX Patcher with Lexical String Ordering and Header Integrity.

Performs zero-overhead, byte-level in-place modifications to DEX files without
risking R8 de-optimization traps or synthetic bridge destruction caused by full-tree
Smali round-trips.

Enforces three non-negotiable invariants:
  1. Equal Byte Length Invariant: UTF-8 byte length of replacement must strictly
     equal the target string to preserve ULEB128 prefixes and absolute offsets.
  2. Monotonic Lexical Interval Invariant: Replacement string S'_k at index k in string_ids
     must satisfy S_{k-1} < S'_k < S_{k+1}. Violations break ART's binary search in
     DexFileLoader, causing silent ClassNotFoundException failures.
  3. Canonical Header Integrity: Header is recalculated (SHA-1 FIRST, Adler-32 LAST)
     and self-verified before returning.
"""

from __future__ import annotations

from pathlib import Path

from noir.infrastructure.dex.integrity import recalculate_dex_header, verify_dex_header
from noir.infrastructure.dex.reader import Dex
from noir.infrastructure.dex.verifier import DexVerifierViolation, verify_dex_bytecode


class DexPatchError(Exception):
    """Base exception for DEX patching failures."""

    pass


class DexStringOrderingError(DexPatchError):
    """Raised when an in-place string patch violates lexical string_ids ordering."""

    pass


def validate_string_ordering(
    entries: list[tuple[int, bytes]],
    target_idx: int,
    new_bytes: bytes,
) -> tuple[bool, str]:
    """Check whether replacing the string at target_idx with new_bytes preserves lexical ordering.

    entries: list of (string_data_offset, raw_string_bytes)
    Returns: (is_valid, reason)
    """
    if not (0 <= target_idx < len(entries)):
        return False, f"Target index {target_idx} out of range [0, {len(entries)})"

    prev_bytes = entries[target_idx - 1][1] if target_idx > 0 else None
    next_bytes = entries[target_idx + 1][1] if target_idx + 1 < len(entries) else None

    if prev_bytes is not None and new_bytes <= prev_bytes:
        return (
            False,
            f"Lexical ordering violation: new string {new_bytes!r} <= predecessor {prev_bytes!r} "
            f"at string_ids[{target_idx - 1}]",
        )

    if next_bytes is not None and new_bytes >= next_bytes:
        return (
            False,
            f"Lexical ordering violation: new string {new_bytes!r} >= successor {next_bytes!r} "
            f"at string_ids[{target_idx + 1}]",
        )

    return True, "OK"


def patch_dex_string_in_place(
    dex_data: bytearray | bytes,
    old_str: str,
    new_str: str,
) -> bytes:
    """Patch a string constant in-place within a DEX binary.

    Args:
        dex_data: Raw DEX binary data.
        old_str: Existing string to replace.
        new_str: New replacement string.

    Returns:
        Patched DEX bytes with recalculated SHA-1 and Adler-32 header.

    Raises:
        ValueError: If old_str and new_str have differing UTF-8 byte lengths.
        DexPatchError: If old_str is not in string_ids or occurs more than once.
        DexStringOrderingError: If new_str violates the lexical interval S_{k-1} < S'_k < S_{k+1}.
    """
    ob = old_str.encode("utf-8")
    nb = new_str.encode("utf-8")

    # Invariant 1: Equal byte length
    if len(ob) != len(nb):
        raise ValueError(
            f"Byte length mismatch: old string is {len(ob)} bytes ({old_str!r}), "
            f"new string is {len(nb)} bytes ({new_str!r}). In-place DEX string patch "
            "requires exactly equal byte length."
        )

    dex = Dex(dex_data)
    entries = dex.read_all_strings()

    target_idx: int | None = None
    for i, (_sdata_off, raw) in enumerate(entries):
        if raw == ob:
            target_idx = i
            break

    if target_idx is None:
        raise DexPatchError(f"Target string {old_str!r} not found in DEX string_ids table")

    # Invariant 2: Exactly 1 occurrence in data
    raw_data = bytes(dex_data)
    count = raw_data.count(ob)
    if count != 1:
        raise DexPatchError(
            f"Target string {old_str!r} has {count} occurrences in DEX data (expected exactly 1)"
        )

    # Invariant 3: Monotonic interval ordering
    ok, err_msg = validate_string_ordering(entries, target_idx, nb)
    if not ok:
        raise DexStringOrderingError(err_msg)

    # Perform in-place byte substitution
    buf = bytearray(raw_data)
    off = buf.find(ob)
    buf[off : off + len(ob)] = nb

    # Invariant 4: Recalculate canonical header (SHA-1 first, Adler-32 last)
    recalculate_dex_header(buf)

    # Verify header
    valid, v_msg = verify_dex_header(buf)
    if not valid:
        raise DexPatchError(f"Header integrity check failed post-patch: {v_msg}")

    return bytes(buf)


def patch_dex_file_string(
    dex_path: str | Path,
    old_str: str,
    new_str: str,
    output_path: str | Path | None = None,
) -> bytes:
    """Patch string in DEX file and optionally write to output path."""
    p = Path(dex_path)
    if not p.is_file():
        raise FileNotFoundError(f"DEX file not found: {p}")

    patched_bytes = patch_dex_string_in_place(p.read_bytes(), old_str, new_str)
    out = Path(output_path) if output_path else p
    out.write_bytes(patched_bytes)
    return patched_bytes


def patch_dex_bytes(
    dex_data: bytearray | bytes,
    offset: int,
    expected_old_bytes: bytes,
    new_bytes: bytes,
    *,
    verify_bytecode: bool = True,
) -> bytes:
    """Apply an equal-length byte patch at a specific offset in a DEX file.

    Validates:
      1. Equal byte length (len(expected_old_bytes) == len(new_bytes)).
      2. Pre-image byte match at offset.
      3. Verifier checks (no new branch targeting move-result*).
      4. Header recalculation and self-verification.
    """
    if len(expected_old_bytes) != len(new_bytes):
        raise ValueError(
            f"Byte length mismatch: expected {len(expected_old_bytes)} bytes, "
            f"got {len(new_bytes)} bytes"
        )

    buf = bytearray(dex_data)
    if offset < 0 or offset + len(expected_old_bytes) > len(buf):
        raise IndexError(f"Patch offset 0x{offset:x} out of range (file size {len(buf)})")

    current = bytes(buf[offset : offset + len(expected_old_bytes)])
    if current != expected_old_bytes:
        raise DexPatchError(
            f"Bytes at offset 0x{offset:x} ({current.hex()}) do not match "
            f"expected ({expected_old_bytes.hex()})"
        )

    buf[offset : offset + len(new_bytes)] = new_bytes

    if verify_bytecode:
        patched_dex = Dex(bytes(buf))
        violations: list[DexVerifierViolation] = verify_dex_bytecode(patched_dex)
        if violations:
            violation_summaries = "; ".join(v.message for v in violations)
            raise DexPatchError(
                f"Bytecode verifier violations introduced by patch: {violation_summaries}"
            )

    recalculate_dex_header(buf)
    valid, v_msg = verify_dex_header(buf)
    if not valid:
        raise DexPatchError(f"Header integrity check failed: {v_msg}")

    return bytes(buf)
