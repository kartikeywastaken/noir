"""Unit tests for Dalvik/ART bytecode verifier (move-result adjacency and branch target constraints)."""

import struct
from pathlib import Path

import pytest

from noir.infrastructure.dex.reader import Dex
from noir.infrastructure.dex.verifier import (
    verify_dex_bytecode,
    verify_dex_file_bytecode,
    verify_method_bytecode,
)

RUNTIME_DEX_PATH = Path(__file__).resolve().parents[2] / "runtime" / "dist" / "noir-runtime-v1.dex"


@pytest.fixture
def real_dex_bytes() -> bytes:
    assert RUNTIME_DEX_PATH.is_file(), f"Missing fixture DEX at {RUNTIME_DEX_PATH}"
    return RUNTIME_DEX_PATH.read_bytes()


def test_clean_runtime_dex_has_zero_violations(real_dex_bytes: bytes):
    """Untouched compiled runtime DEX passes verifier with 0 violations."""
    dex = Dex(real_dex_bytes)
    violations = verify_dex_bytecode(dex)
    assert len(violations) == 0, f"Unexpected violations in clean DEX: {violations}"


def test_conditional_branch_targeting_move_result_rejected(real_dex_bytes: bytes):
    """Detect when a conditional branch (if-eqz) jumps directly onto a move-result* opcode."""
    data = bytearray(real_dex_bytes)
    # Lin/v0id/noir/injected/RuntimeConfig;->flag has code_off=0xf84
    # insns: 0xf94 iget-object (4B), 0xf98 invoke-virtual (6B), 0xf9e move-result (2B)
    # Replace 0xf94 with if-eqz v0, +5 (landing on 0xf9e: delta = 10B = 5 code units)
    data[0xF94:0xF98] = struct.pack("<HH", 0x0038, 5)

    dex = Dex(data)
    violations = verify_method_bytecode(
        dex,
        0xF84,
        class_name="Lin/v0id/noir/injected/RuntimeConfig;",
        method_name="flag",
        method_desc="(Ljava/lang/String;Z)Z",
    )
    assert len(violations) >= 1
    branch_violation = next(v for v in violations if v.violation_type == "branch_target")
    assert branch_violation.branch_offset == 0xF94
    assert branch_violation.move_result_offset == 0xF9E
    assert branch_violation.move_result_opcode == 0x0A  # move-result
    assert "if-eqz" in branch_violation.message
    assert "VerifyError" in branch_violation.message

    # Test dictionary serialization
    d = branch_violation.to_dict()
    assert d["violation_type"] == "branch_target"
    assert d["move_result_offset"] == "0xf9e"


def test_unconditional_branch_targeting_move_result_rejected(real_dex_bytes: bytes):
    """Detect when an unconditional goto jumps directly onto a move-result* opcode."""
    data = bytearray(real_dex_bytes)
    # Replace 0xf94 with goto +5 (10B = 5 units) plus nop padding (2B)
    data[0xF94:0xF98] = b"\x28\x05\x00\x00"

    dex = Dex(data)
    violations = verify_method_bytecode(
        dex,
        0xF84,
        class_name="Lin/v0id/noir/injected/RuntimeConfig;",
        method_name="flag",
        method_desc="(Ljava/lang/String;Z)Z",
    )
    assert len(violations) >= 1
    branch_violation = next(v for v in violations if v.violation_type == "branch_target")
    assert branch_violation.branch_offset == 0xF94
    assert branch_violation.move_result_offset == 0xF9E
    assert branch_violation.branch_opcode == 0x28  # goto


def test_move_result_preceded_by_non_producer_rejected(real_dex_bytes: bytes):
    """Detect when move-result is preceded by a non-producer instruction (e.g. nop)."""
    data = bytearray(real_dex_bytes)
    # Replace invoke-virtual at 0xf98:0xf9e with 3 nops
    data[0xF98:0xF9E] = b"\x00\x00\x00\x00\x00\x00"

    dex = Dex(data)
    violations = verify_method_bytecode(
        dex,
        0xF84,
        class_name="Lin/v0id/noir/injected/RuntimeConfig;",
        method_name="flag",
        method_desc="(Ljava/lang/String;Z)Z",
    )
    assert any(v.violation_type == "illegal_predecessor" for v in violations)
    pred_violation = next(v for v in violations if v.violation_type == "illegal_predecessor")
    assert pred_violation.move_result_offset == 0xF9E
    assert "nop" in pred_violation.message


def test_move_result_as_first_instruction_rejected(real_dex_bytes: bytes):
    """Detect when move-result appears at the very beginning of a method (missing producer)."""
    data = bytearray(real_dex_bytes)
    # Place move-result v0 (0x0a 0x00) at insns_off = 0xf94
    data[0xF94:0xF96] = b"\x0a\x00"

    dex = Dex(data)
    violations = verify_method_bytecode(
        dex,
        0xF84,
        class_name="Lin/v0id/noir/injected/RuntimeConfig;",
        method_name="flag",
        method_desc="(Ljava/lang/String;Z)Z",
    )
    assert any(v.violation_type == "missing_producer" for v in violations)


def test_branch_targeting_instruction_after_move_result_passes(real_dex_bytes: bytes):
    """A branch targeting instructions AFTER move-result (e.g. return) is legitimate."""
    data = bytearray(real_dex_bytes)
    # 0xfa0 is return opcode (offset delta from 0xf94 is 12 bytes = 6 units)
    # Replace 0xf94 with if-eqz v0, +6 (targeting 0xfa0 return)
    data[0xF94:0xF98] = struct.pack("<HH", 0x0038, 6)

    dex = Dex(data)
    violations = verify_method_bytecode(
        dex,
        0xF84,
        class_name="Lin/v0id/noir/injected/RuntimeConfig;",
        method_name="flag",
        method_desc="(Ljava/lang/String;Z)Z",
    )
    # No branch_target violations because 0xfa0 is return, not move-result
    assert not any(v.violation_type == "branch_target" for v in violations)


def test_verify_dex_file_bytecode_convenience(tmp_path: Path, real_dex_bytes: bytes):
    """Test top-level verify_dex_file_bytecode entrypoint."""
    dex_path = tmp_path / "runtime.dex"
    dex_path.write_bytes(real_dex_bytes)

    violations = verify_dex_file_bytecode(dex_path)
    assert len(violations) == 0
