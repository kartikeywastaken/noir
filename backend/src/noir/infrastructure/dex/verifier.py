"""Dalvik/ART Bytecode Verifier.

Enforces bytecode legality constraints for Android Runtime (ART):
  1. Producer-Consumer Adjacency: Every `move-result*` opcode (0x0A, 0x0B, 0x0C) must
     be the immediate successor of a result producer (invoke-* or filled-new-array*).
  2. Branch Target Legality: No control-flow branch (if-*, goto*, switches) can land
     directly on a `move-result*` opcode, preventing ART VerifyError:
     `copyRes vN <- result0 type=Undefined`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noir.infrastructure.dex.reader import (
    MOVE_RESULT_OPS,
    PRODUCER_OPS,
    Dex,
    load_dex,
)


@dataclass(frozen=True)
class DexVerifierViolation:
    """Represents a verifier rule violation in Dalvik bytecode."""

    class_name: str
    method_name: str
    method_desc: str
    violation_type: str  # "branch_target" | "illegal_predecessor" | "missing_producer"
    move_result_offset: int
    move_result_opcode: int
    branch_offset: int | None = None
    branch_opcode: int | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "class_name": self.class_name,
            "method_name": self.method_name,
            "method_desc": self.method_desc,
            "violation_type": self.violation_type,
            "move_result_offset": f"0x{self.move_result_offset:x}",
            "move_result_opcode": f"0x{self.move_result_opcode:02x}",
            "branch_offset": f"0x{self.branch_offset:x}"
            if self.branch_offset is not None
            else None,
            "branch_opcode": f"0x{self.branch_opcode:02x}"
            if self.branch_opcode is not None
            else None,
            "message": self.message,
        }


def verify_method_bytecode(
    dex: Dex,
    code_off: int,
    class_name: str = "",
    method_name: str = "",
    method_desc: str = "",
) -> list[DexVerifierViolation]:
    """Verify bytecode legality for a single method body."""
    insns = list(dex.decode(code_off))
    if not insns:
        return []

    violations: list[DexVerifierViolation] = []
    mr_map = {i["off"]: i for i in insns if i["op"] in MOVE_RESULT_OPS}

    # Check 1: Adjacency - every move-result must be preceded immediately by a producer
    for idx, insn in enumerate(insns):
        if insn["op"] not in MOVE_RESULT_OPS:
            continue

        mr_off = insn["off"]
        mr_op = insn["op"]

        if idx == 0:
            violations.append(
                DexVerifierViolation(
                    class_name=class_name,
                    method_name=method_name,
                    method_desc=method_desc,
                    violation_type="missing_producer",
                    move_result_offset=mr_off,
                    move_result_opcode=mr_op,
                    message=(
                        f"Opcode '{insn['name']}' at offset 0x{mr_off:x} is the first instruction "
                        "of method; requires preceding invoke-* or filled-new-array."
                    ),
                )
            )
            continue

        prev_insn = insns[idx - 1]
        if prev_insn["op"] not in PRODUCER_OPS:
            violations.append(
                DexVerifierViolation(
                    class_name=class_name,
                    method_name=method_name,
                    method_desc=method_desc,
                    violation_type="illegal_predecessor",
                    move_result_offset=mr_off,
                    move_result_opcode=mr_op,
                    message=(
                        f"Opcode '{insn['name']}' at offset 0x{mr_off:x} is preceded by "
                        f"'{prev_insn['name']}' (0x{prev_insn['op']:02x}) "
                        f"at offset 0x{prev_insn['off']:x}. "
                        "Dalvik verifier requires move-result* to immediately succeed a producer."
                    ),
                )
            )

    # Check 2: Branch target legality - no branch may land directly on move-result*
    if mr_map:
        for insn in insns:
            tgt = dex.branch_target(insn)
            if tgt is not None and tgt in mr_map:
                target_mr = mr_map[tgt]
                violations.append(
                    DexVerifierViolation(
                        class_name=class_name,
                        method_name=method_name,
                        method_desc=method_desc,
                        violation_type="branch_target",
                        move_result_offset=tgt,
                        move_result_opcode=target_mr["op"],
                        branch_offset=insn["off"],
                        branch_opcode=insn["op"],
                        message=(
                            f"Branch instruction '{insn['name']}' at offset 0x{insn['off']:x} "
                            f"targets '{target_mr['name']}' at offset 0x{tgt:x}. ART verifier "
                            "will reject class with VerifyError (copyRes <- result0 type=Undefined)."
                        ),
                    )
                )

            # Check switch instruction targets
            for sw_tgt in dex.switch_targets(insn):
                if sw_tgt in mr_map:
                    target_mr = mr_map[sw_tgt]
                    violations.append(
                        DexVerifierViolation(
                            class_name=class_name,
                            method_name=method_name,
                            method_desc=method_desc,
                            violation_type="branch_target",
                            move_result_offset=sw_tgt,
                            move_result_opcode=target_mr["op"],
                            branch_offset=insn["off"],
                            branch_opcode=insn["op"],
                            message=(
                                f"Switch instruction '{insn['name']}' at offset 0x{insn['off']:x} "
                                f"targets '{target_mr['name']}' at offset 0x{sw_tgt:x}. ART "
                                "verifier will reject class with VerifyError."
                            ),
                        )
                    )

    return violations


def verify_dex_bytecode(dex: Dex) -> list[DexVerifierViolation]:
    """Scan all classes and methods in a DEX image for verifier violations."""
    all_violations: list[DexVerifierViolation] = []
    for fqcn in dex.class_names():
        try:
            methods = list(dex.methods_of(fqcn))
        except KeyError:
            continue

        for _section, _midx, _cls, name, desc, code_off in methods:
            if code_off == 0:
                continue
            violations = verify_method_bytecode(
                dex=dex,
                code_off=code_off,
                class_name=fqcn,
                method_name=name,
                method_desc=desc,
            )
            all_violations.extend(violations)

    return all_violations


def verify_dex_file_bytecode(source: str | Path | bytes | bytearray) -> list[DexVerifierViolation]:
    """Load DEX and return list of all bytecode verifier violations."""
    dex, _entry = load_dex(source)
    return verify_dex_bytecode(dex)
