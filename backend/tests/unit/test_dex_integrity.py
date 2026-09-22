"""Unit tests for canonical DEX header integrity and recalculation order."""

import hashlib
import struct
import zipfile
from pathlib import Path

import pytest

from noir.infrastructure.dex.integrity import (
    compute_dex_checksum,
    compute_dex_signature,
    recalculate_dex_header,
    verify_dex_file,
    verify_dex_header,
    verify_dex_header_fields,
)
from noir.validation.apk_validator import ApkValidationError, validate_apk, validate_apk_dex_headers

RUNTIME_DEX_PATH = Path(__file__).resolve().parents[2] / "runtime" / "dist" / "noir-runtime-v1.dex"


@pytest.fixture
def real_dex_bytes() -> bytes:
    assert RUNTIME_DEX_PATH.is_file(), f"Missing fixture DEX at {RUNTIME_DEX_PATH}"
    return RUNTIME_DEX_PATH.read_bytes()


def test_untouched_dex_passes_integrity_check(real_dex_bytes: bytes):
    """Untouched compiled DEX passes SHA-1 and Adler-32 verification."""
    is_valid, msg = verify_dex_header(real_dex_bytes)
    assert is_valid is True, f"Untouched DEX failed verification: {msg}"
    assert msg == "OK"

    chk_ok, sig_ok = verify_dex_header_fields(real_dex_bytes)
    assert chk_ok is True
    assert sig_ok is True


def test_signature_and_checksum_calculation(real_dex_bytes: bytes):
    """Confirm signature covers data[32:] and checksum covers data[12:]."""
    sig = compute_dex_signature(real_dex_bytes)
    assert len(sig) == 20
    assert sig == hashlib.sha1(real_dex_bytes[32:]).digest()  # noqa: S324
    assert sig == real_dex_bytes[12:32]

    chk = compute_dex_checksum(real_dex_bytes)
    stored_chk = struct.unpack_from("<I", real_dex_bytes, 8)[0]
    assert chk == stored_chk


def test_corrupted_checksum_detected(real_dex_bytes: bytes):
    """Altering checksum field causes verify_dex_header to report Bad checksum."""
    data = bytearray(real_dex_bytes)
    struct.pack_into("<I", data, 8, 0xDEADBEEF)

    is_valid, msg = verify_dex_header(data)
    assert is_valid is False
    assert "Bad checksum" in msg
    assert "0xdeadbeef" in msg.lower()


def test_corrupted_signature_detected(real_dex_bytes: bytes):
    """Altering signature field causes verify_dex_header to report Signature mismatch."""
    data = bytearray(real_dex_bytes)
    data[12:32] = b"\x00" * 20

    is_valid, msg = verify_dex_header(data)
    assert is_valid is False
    assert "Signature mismatch" in msg


def test_corrupted_payload_detected(real_dex_bytes: bytes):
    """Modifying bytes in payload causes header mismatch."""
    data = bytearray(real_dex_bytes)
    # Flip bytes in data section
    data[200] ^= 0xFF

    is_valid, msg = verify_dex_header(data)
    assert is_valid is False


def test_canonical_recalculation_fixes_header(real_dex_bytes: bytes):
    """recalculate_dex_header repairs both signature and checksum in canonical order."""
    data = bytearray(real_dex_bytes)
    # Corrupt both header fields and a data byte
    data[8:12] = b"\x00" * 4
    data[12:32] = b"\x00" * 20
    data[500] = (data[500] + 1) % 256

    recalculated = recalculate_dex_header(data)
    is_valid, msg = verify_dex_header(recalculated)
    assert is_valid is True, f"Repaired header failed: {msg}"
    assert msg == "OK"


def test_inverted_recalculation_fails_with_bad_checksum(real_dex_bytes: bytes):
    """Computing Adler-32 BEFORE SHA-1 leaves checksum stale and produces Bad checksum.

    This mathematically proves why canonical order (SHA-1 FIRST, Adler-32 LAST) is mandatory.
    """
    import zlib

    data = bytearray(real_dex_bytes)
    # Zero out signature and checksum
    data[8:12] = b"\x00" * 4
    data[12:32] = b"\x00" * 20

    # WRONG ORDER: Checksum computed while signature is zeros
    wrong_chk = zlib.adler32(bytes(data[12:])) & 0xFFFFFFFF
    struct.pack_into("<I", data, 8, wrong_chk)

    # Signature written second - mutates data[12:32] after checksum was frozen
    sig = hashlib.sha1(bytes(data[32:])).digest()  # noqa: S324
    data[12:32] = sig

    # Verification must fail with Bad checksum
    is_valid, msg = verify_dex_header(data)
    assert is_valid is False
    assert "Bad checksum" in msg


def test_verify_dex_file_on_disk(tmp_path: Path, real_dex_bytes: bytes):
    """verify_dex_file inspects file path."""
    dex_file = tmp_path / "test.dex"
    dex_file.write_bytes(real_dex_bytes)

    valid, msg = verify_dex_file(dex_file)
    assert valid is True
    assert msg == "OK"

    # Corrupt on disk
    dex_file.write_bytes(real_dex_bytes[:100])
    valid, msg = verify_dex_file(dex_file)
    assert valid is False


def test_container_apk_dex_validation(tmp_path: Path, real_dex_bytes: bytes):
    """Test container-level verification of DEX headers in APK archives."""
    apk_file = tmp_path / "sample.apk"
    with zipfile.ZipFile(apk_file, "w") as zf:
        zf.writestr("AndroidManifest.xml", "<manifest/>")
        zf.writestr("classes.dex", real_dex_bytes)
        zf.writestr("classes2.dex", real_dex_bytes)

    dex_status = validate_apk_dex_headers(apk_file)
    assert "classes.dex" in dex_status
    assert "classes2.dex" in dex_status
    assert dex_status["classes.dex"][0] is True
    assert dex_status["classes2.dex"][0] is True

    result = validate_apk(apk_file, require_valid_dex_headers=True)
    assert result["valid"] is True
    assert result["dex_verification"]["classes.dex"]["valid"] is True
    assert result["dex_verification"]["classes2.dex"]["valid"] is True


def test_container_apk_corrupted_dex_rejected_when_strict(tmp_path: Path, real_dex_bytes: bytes):
    """Test that strict APK validation catches corrupted DEX headers."""
    corrupted_dex = bytearray(real_dex_bytes)
    corrupted_dex[8:12] = b"\x11\x22\x33\x44"  # Corrupt checksum

    apk_file = tmp_path / "corrupted.apk"
    with zipfile.ZipFile(apk_file, "w") as zf:
        zf.writestr("AndroidManifest.xml", "<manifest/>")
        zf.writestr("classes.dex", corrupted_dex)

    # Non-strict mode registers warning
    res = validate_apk(apk_file, require_valid_dex_headers=False)
    assert res["valid"] is True
    assert any("Bad checksum" in w for w in res["warnings"])
    assert res["dex_verification"]["classes.dex"]["valid"] is False

    # Strict mode raises ApkValidationError
    with pytest.raises(ApkValidationError) as exc_info:
        validate_apk(apk_file, require_valid_dex_headers=True)
    assert "corrupted_dex_header" in str(exc_info.value.code)
