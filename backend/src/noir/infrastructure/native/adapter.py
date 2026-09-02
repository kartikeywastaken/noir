"""Conservative, same-length Android ELF patch primitives.

Offsets are file offsets. Every mutation is restricted to an executable PT_LOAD
segment, validates exact preimage bytes, and must disassemble completely afterward.
"""

from __future__ import annotations

import hashlib
import re
import struct
from itertools import islice
from pathlib import Path
from typing import Any


class NativePatchError(Exception):
    """Raised when an ELF operation is missing, ambiguous, or structurally unsafe."""


_ABI_ARCHITECTURES = {
    "arm64-v8a": "arm64",
    "armeabi-v7a": "arm",
    "x86": "x86",
    "x86_64": "x86_64",
}


def _dependencies():
    try:
        import capstone  # type: ignore[import-untyped]
        import lief
    except ImportError as exc:
        raise NativePatchError("Native support requires the lief and capstone packages") from exc
    return lief, capstone


def _parse_elf(path: Path):
    lief, _ = _dependencies()
    if not path.is_file():
        raise NativePatchError(f"Native library not found: {path.name}")
    binary = lief.parse(str(path))
    if binary is None or binary.format != lief.Binary.FORMATS.ELF:
        raise NativePatchError("Target is not a valid ELF binary")
    return binary


def _abi_from_binary(binary) -> str:
    machine = str(binary.header.machine_type).upper()
    clazz = str(binary.header.identity_class).upper()
    if "AARCH64" in machine:
        return "arm64-v8a"
    if "ARM" in machine:
        return "armeabi-v7a"
    if "X86_64" in machine or "X86-64" in machine:
        return "x86_64"
    if "I386" in machine or "X86" in machine:
        return "x86" if "32" in clazz else "x86_64"
    raise NativePatchError(f"Unsupported ELF architecture: {machine}")


def _executable_mapping(binary, offset: int, length: int):
    if offset < 0 or length <= 0:
        raise NativePatchError("Native offset and length must be positive")
    end = offset + length
    for segment in binary.segments:
        start = int(segment.file_offset)
        stop = start + int(segment.physical_size)
        flags = str(segment.flags).upper()
        if start <= offset and end <= stop and "X" in flags:
            return int(segment.virtual_address) + (offset - start)
    raise NativePatchError("Patch range is not wholly inside an executable ELF segment")


def _capstone(abi: str):
    _, capstone = _dependencies()
    if abi == "arm64-v8a":
        return capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    if abi == "armeabi-v7a":
        return capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
    if abi == "x86":
        return capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    if abi == "x86_64":
        return capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    raise NativePatchError(f"Unsupported ABI: {abi}")


def _keystone(abi: str):
    try:
        import keystone  # type: ignore[import-untyped]

        if abi == "arm64-v8a":
            return keystone.Ks(keystone.KS_ARCH_ARM64, keystone.KS_MODE_LITTLE_ENDIAN)
        if abi == "armeabi-v7a":
            return keystone.Ks(
                keystone.KS_ARCH_ARM,
                keystone.KS_MODE_ARM | keystone.KS_MODE_LITTLE_ENDIAN,
            )
        if abi == "x86":
            return keystone.Ks(keystone.KS_ARCH_X86, keystone.KS_MODE_32)
        if abi == "x86_64":
            return keystone.Ks(keystone.KS_ARCH_X86, keystone.KS_MODE_64)
    except (ImportError, OSError):
        # keystone-engine does not publish a macOS arm64 library. Keep the
        # operation surface deterministic with equivalent encodings for only
        # the three templates NOIR exposes; never become a general assembler.
        return _BoundedAssembler(abi)
    raise NativePatchError(f"Unsupported ABI: {abi}")


class _BoundedAssembler:
    def __init__(self, abi: str):
        if abi not in _ABI_ARCHITECTURES:
            raise NativePatchError(f"Unsupported ABI: {abi}")
        self.abi = abi

    def asm(self, source: str, addr: int = 0):
        output = bytearray()
        current = addr
        for statement in [item.strip().lower() for item in source.split(";") if item.strip()]:
            encoded = self._statement(statement, current)
            output.extend(encoded)
            current += len(encoded)
        return list(output), len(output)

    def _statement(self, statement: str, address: int) -> bytes:
        if statement == "nop":
            return {
                "arm64-v8a": bytes.fromhex("1f2003d5"),
                "armeabi-v7a": bytes.fromhex("00f020e3"),
                "x86": b"\x90",
                "x86_64": b"\x90",
            }[self.abi]
        if statement == "ret" and self.abi == "arm64-v8a":
            return bytes.fromhex("c0035fd6")
        if statement == "bx lr" and self.abi == "armeabi-v7a":
            return bytes.fromhex("1eff2fe1")
        if statement == "ret" and self.abi in {"x86", "x86_64"}:
            return b"\xc3"
        immediate = re.fullmatch(r"mov (?:w0|r0|eax),\s*#?(0x[0-9a-f]+|\d+)", statement)
        if immediate:
            value = int(immediate.group(1), 0)
            if self.abi == "arm64-v8a" and value <= 0xFFFF:
                return struct.pack("<I", 0x52800000 | (value << 5))
            if self.abi == "armeabi-v7a" and value <= 0xFF:
                return struct.pack("<I", 0xE3A00000 | value)
            if self.abi in {"x86", "x86_64"} and value <= 0xFFFFFFFF:
                return b"\xb8" + struct.pack("<I", value)
        arm64_wide = re.fullmatch(
            r"mov([zk]) w0,\s*#?(0x[0-9a-f]+|\d+)(?:,\s*lsl #16)?",
            statement,
        )
        if arm64_wide and self.abi == "arm64-v8a":
            value = int(arm64_wide.group(2), 0)
            shifted = statement.endswith("lsl #16")
            base = 0x72800000 if arm64_wide.group(1) == "k" else 0x52800000
            if shifted:
                base |= 1 << 21
            return struct.pack("<I", base | ((value & 0xFFFF) << 5))
        arm_wide = re.fullmatch(r"mov([wt]) r0,\s*#?(0x[0-9a-f]+|\d+)", statement)
        if arm_wide and self.abi == "armeabi-v7a":
            value = int(arm_wide.group(2), 0) & 0xFFFF
            base = 0xE3400000 if arm_wide.group(1) == "t" else 0xE3000000
            return struct.pack("<I", base | ((value >> 12) << 16) | (value & 0xFFF))
        branch = re.fullmatch(r"(?:b|jmp)\s+(0x[0-9a-f]+|\d+)", statement)
        if branch:
            target = int(branch.group(1), 0)
            if self.abi == "arm64-v8a":
                delta = target - address
                if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
                    raise NativePatchError("ARM64 branch target is out of range or unaligned")
                return struct.pack("<I", 0x14000000 | ((delta // 4) & 0x03FFFFFF))
            if self.abi == "armeabi-v7a":
                delta = target - (address + 8)
                if delta % 4 or not -(1 << 25) <= delta < (1 << 25):
                    raise NativePatchError("ARM branch target is out of range or unaligned")
                return struct.pack("<I", 0xEA000000 | ((delta // 4) & 0x00FFFFFF))
            delta = target - (address + 5)
            if not -(1 << 31) <= delta < (1 << 31):
                raise NativePatchError("x86 branch target is out of range")
            return b"\xe9" + struct.pack("<i", delta)
        raise NativePatchError(f"Bounded assembler does not support: {statement}")


def inspect_elf(path: Path) -> dict[str, Any]:
    """Return bounded ABI, symbol, dependency, and section information."""
    binary = _parse_elf(path)
    raw_exported = sorted(
        (symbol for symbol in binary.exported_symbols if symbol.name),
        key=lambda symbol: symbol.name,
    )[:2_000]
    exported_by_location: dict[tuple[str, int, int], Any] = {}
    for symbol in raw_exported:
        exported_by_location.setdefault((symbol.name, int(symbol.value), int(symbol.size)), symbol)
    exported = list(exported_by_location.values())
    exports = sorted({symbol.name for symbol in exported})
    imports = sorted(symbol.name for symbol in binary.imported_symbols if symbol.name)[:2_000]
    raw = path.read_bytes()
    symbol_details = []
    for symbol in exported:
        address, size = int(symbol.value), int(symbol.size)
        if address <= 0 or size <= 0:
            continue
        for segment in binary.segments:
            start = int(segment.virtual_address)
            stop = start + int(segment.physical_size)
            if start <= address < stop and address + size <= stop:
                offset = int(segment.file_offset) + address - start
                symbol_details.append(
                    {
                        "name": symbol.name,
                        "address": address,
                        "file_offset": offset,
                        "size": size,
                        "sha256": hashlib.sha256(raw[offset : offset + size]).hexdigest(),
                    }
                )
                break
        if len(symbol_details) >= 512:
            break
    return {
        "path": path.name,
        "abi": _abi_from_binary(binary),
        "entrypoint": int(binary.entrypoint),
        "soname": next(
            (str(entry) for entry in binary.dynamic_entries if "SONAME" in str(entry)), ""
        ),
        "libraries": [str(name) for name in binary.libraries],
        "exports": exports,
        "symbol_details": symbol_details,
        "imports": imports,
        "sections": [
            {
                "name": section.name,
                "offset": int(section.offset),
                "size": int(section.size),
            }
            for section in islice(binary.sections, 512)
        ],
    }


def disassemble_range(
    path: Path, offset: int, length: int, *, abi: str | None = None
) -> list[dict]:
    """Disassemble one bounded executable file range and require complete coverage."""
    if length > 4096:
        raise NativePatchError("Native disassembly window exceeds 4 KiB")
    binary = _parse_elf(path)
    detected = _abi_from_binary(binary)
    if abi and abi != detected:
        raise NativePatchError(f"Declared ABI {abi} does not match ELF ABI {detected}")
    address = _executable_mapping(binary, offset, length)
    data = path.read_bytes()[offset : offset + length]
    instructions = list(_capstone(detected).disasm(data, address))
    if not instructions or sum(instruction.size for instruction in instructions) != length:
        raise NativePatchError("Patch range does not form a complete valid instruction sequence")
    return [
        {
            "address": int(instruction.address),
            "size": int(instruction.size),
            "bytes": bytes(instruction.bytes).hex(),
            "mnemonic": instruction.mnemonic,
            "operands": instruction.op_str,
        }
        for instruction in instructions
    ]


def range_hash(path: Path, offset: int, length: int) -> str:
    data = path.read_bytes()
    if offset < 0 or length <= 0 or offset + length > len(data):
        raise NativePatchError("Native patch range is outside the file")
    return hashlib.sha256(data[offset : offset + length]).hexdigest()


def apply_byte_patch(path: Path, offset: int, expected_bytes: bytes, new_bytes: bytes) -> None:
    """Apply an exact same-length patch in an executable segment."""
    if not expected_bytes or len(new_bytes) != len(expected_bytes):
        raise NativePatchError("Native byte patches must be nonempty and same-length")
    binary = _parse_elf(path)
    _executable_mapping(binary, offset, len(expected_bytes))
    content = bytearray(path.read_bytes())
    actual = bytes(content[offset : offset + len(expected_bytes)])
    if actual != expected_bytes:
        raise NativePatchError("Native byte preimage does not match")
    content[offset : offset + len(expected_bytes)] = new_bytes
    path.write_bytes(content)


def apply_nop_range(path: Path, offset: int, length: int, expected_bytes: bytes, abi: str) -> None:
    """Replace complete instructions with architecture-correct NOP instructions."""
    if len(expected_bytes) != length:
        raise NativePatchError("NOP preimage length does not match native_length")
    unit = 1 if abi in {"x86", "x86_64"} else 4
    if length <= 0 or length % unit:
        raise NativePatchError(f"NOP length must be a positive multiple of {unit} for {abi}")
    assembler = _keystone(abi)
    try:
        encoding, _ = assembler.asm("; ".join(["nop"] * (length // unit)))
    except Exception as exc:
        raise NativePatchError(f"Unable to assemble {abi} NOP range") from exc
    replacement = bytes(encoding)
    if len(replacement) != length:
        raise NativePatchError("Assembler produced a non-length-preserving NOP range")
    apply_byte_patch(path, offset, expected_bytes, replacement)


def apply_branch_redirect(
    path: Path,
    offset: int,
    target_offset: int,
    expected_bytes: bytes,
    abi: str,
) -> None:
    """Replace exactly one existing branch with a same-length unconditional branch."""
    binary = _parse_elf(path)
    address = _executable_mapping(binary, offset, len(expected_bytes))
    target_address = _executable_mapping(binary, target_offset, 1)
    current = disassemble_range(path, offset, len(expected_bytes), abi=abi)
    if len(current) != 1 or not current[0]["mnemonic"].lower().startswith(("b", "j")):
        raise NativePatchError("Branch redirect target must be exactly one branch instruction")
    assembler = _keystone(abi)
    mnemonic = "b" if abi.startswith("arm") else "jmp"
    try:
        encoding, _ = assembler.asm(f"{mnemonic} 0x{target_address:x}", addr=address)
    except Exception as exc:
        raise NativePatchError("Redirect target is outside the architecture branch range") from exc
    replacement = bytes(encoding)
    if len(replacement) != len(expected_bytes):
        raise NativePatchError("Redirect is not length-preserving")
    apply_byte_patch(path, offset, expected_bytes, replacement)


def assemble_force_return(abi: str, value: int, length: int, address: int) -> bytes:
    """Assemble a bounded constant integer return and pad with valid NOPs."""
    if not -(2**31) <= value < 2**32:
        raise NativePatchError("IL2CPP return constant must fit in 32 bits")
    assembler = _keystone(abi)
    unsigned = value & 0xFFFFFFFF
    low, high = unsigned & 0xFFFF, unsigned >> 16
    if abi == "arm64-v8a":
        source = f"movz w0, #{low}"
        if high:
            source += f"; movk w0, #{high}, lsl #16"
        source += "; ret"
    elif abi == "armeabi-v7a":
        source = f"movw r0, #{low}"
        if high:
            source += f"; movt r0, #{high}"
        source += "; bx lr"
    else:
        source = f"mov eax, {unsigned}; ret"
    try:
        encoding, _ = assembler.asm(source, addr=address)
    except Exception as exc:
        raise NativePatchError("Unable to assemble constant-return trampoline") from exc
    result = bytes(encoding)
    if len(result) > length:
        raise NativePatchError("IL2CPP method is too short for a constant-return trampoline")
    remaining = length - len(result)
    if remaining:
        unit = 1 if abi in {"x86", "x86_64"} else 4
        if remaining % unit:
            raise NativePatchError("Function patch window cannot be padded with complete NOPs")
        padding, _ = assembler.asm("; ".join(["nop"] * (remaining // unit)))
        result += bytes(padding)
    if len(result) != length:
        raise NativePatchError("Constant-return trampoline is not length preserving")
    return result


def verify_elf(path: Path) -> dict[str, Any]:
    """Reparse the complete ELF after mutation."""
    return inspect_elf(path)
