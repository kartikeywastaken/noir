"""DEX Header Integrity and Hash Recalculation.

Enforces canonical recalculation order:
  1. SHA-1 signature over data[32:] FIRST, stored at offset 12..31
  2. Adler-32 checksum over data[12:] LAST, stored as 32-bit uint LE at offset 8..11

Because Adler-32 covers data[12:], it encompasses the newly computed SHA-1 signature.
Calculating Adler-32 before SHA-1 leaves the signature field stale, producing
"Bad checksum" failures in the Android Runtime (ART).
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path


def compute_dex_signature(data: bytes) -> bytes:
    """Compute 20-byte SHA-1 signature over data[32:]."""
    if len(data) < 32:
        raise ValueError(f"DEX data too short for signature calculation: {len(data)} bytes")
    return hashlib.sha1(data[32:]).digest()  # noqa: S324 - DEX format specification requires SHA-1


def compute_dex_checksum(data: bytes) -> int:
    """Compute 32-bit unsigned Adler-32 checksum over data[12:]."""
    if len(data) < 12:
        raise ValueError(f"DEX data too short for checksum calculation: {len(data)} bytes")
    return zlib.adler32(data[12:]) & 0xFFFFFFFF


def recalculate_dex_header(data: bytearray | bytes) -> bytes:
    """Recalculate DEX header signature and checksum in canonical order.

    Order:
      1. SHA-1 signature over data[32:] written to data[12:32] FIRST
      2. Adler-32 checksum over data[12:] written to data[8:12] LAST
    """
    buf = bytearray(data)
    if len(buf) < 0x70:
        raise ValueError(f"DEX data smaller than minimum header size: {len(buf)} bytes")

    # Step 1: SHA-1 signature FIRST
    sig = compute_dex_signature(bytes(buf))
    buf[12:32] = sig

    # Step 2: Adler-32 checksum LAST
    chk = compute_dex_checksum(bytes(buf))
    struct.pack_into("<I", buf, 8, chk)

    if isinstance(data, bytearray):
        data[:] = buf

    return bytes(buf)


def verify_dex_header(data: bytes | bytearray) -> tuple[bool, str]:
    """Verify whether DEX header signature and checksum match the data.

    Returns (is_valid, message).
    """
    raw = bytes(data)
    if len(raw) < 0x70:
        return False, f"DEX data smaller than header: {len(raw)} bytes (minimum 112)"

    if not raw.startswith(b"dex\n"):
        return False, f"Invalid DEX magic: {raw[:4]!r}"

    stored_sig = raw[12:32]
    expected_sig = compute_dex_signature(raw)
    if stored_sig != expected_sig:
        return (
            False,
            f"Signature mismatch: stored={stored_sig.hex()}, computed={expected_sig.hex()}",
        )

    stored_chk = struct.unpack_from("<I", raw, 8)[0]
    expected_chk = compute_dex_checksum(raw)
    if stored_chk != expected_chk:
        return False, f"Bad checksum: stored=0x{stored_chk:08x}, computed=0x{expected_chk:08x}"

    return True, "OK"


def verify_dex_header_fields(data: bytes | bytearray) -> tuple[bool, bool]:
    """Return (checksum_ok, signature_ok) boolean tuple."""
    raw = bytes(data)
    if len(raw) < 0x70 or not raw.startswith(b"dex\n"):
        return False, False
    stored_chk = struct.unpack_from("<I", raw, 8)[0]
    expected_chk = compute_dex_checksum(raw)
    stored_sig = raw[12:32]
    expected_sig = compute_dex_signature(raw)
    return (stored_chk == expected_chk), (stored_sig == expected_sig)


def verify_dex_file(path: str | Path) -> tuple[bool, str]:
    """Verify header of a DEX file on disk."""
    p = Path(path)
    if not p.is_file():
        return False, f"File not found: {p}"
    return verify_dex_header(p.read_bytes())
