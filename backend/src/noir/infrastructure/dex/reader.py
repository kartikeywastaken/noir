"""DEX Binary Reader and Instruction Decoder.

Provides complete parsing of Dalvik Executable (DEX) files, including header fields,
string table, type/proto/field/method tables, class definitions, and exact forward
instruction decoding with absolute branch target computation.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# Verified DEX header field byte offsets
HEADER_OFFSETS: dict[str, int] = {
    "file_size": 0x20,
    "header_size": 0x24,
    "endian_tag": 0x28,
    "link_size": 0x2C,
    "link_off": 0x30,
    "map_off": 0x34,
    "string_ids_size": 0x38,
    "string_ids_off": 0x3C,
    "type_ids_size": 0x40,
    "type_ids_off": 0x44,
    "proto_ids_size": 0x48,
    "proto_ids_off": 0x4C,
    "field_ids_size": 0x50,
    "field_ids_off": 0x54,
    "method_ids_size": 0x58,
    "method_ids_off": 0x5C,
    "class_defs_size": 0x60,
    "class_defs_off": 0x64,
    "data_size": 0x68,
    "data_off": 0x6C,
}

OP_NAMES: dict[int, str] = {
    0x00: "nop",
    0x01: "move",
    0x02: "move/from16",
    0x03: "move/16",
    0x04: "move-wide",
    0x05: "move-wide/from16",
    0x06: "move-wide/16",
    0x07: "move-object",
    0x08: "move-object/from16",
    0x09: "move-object/16",
    0x0A: "move-result",
    0x0B: "move-result-wide",
    0x0C: "move-result-object",
    0x0D: "move-exception",
    0x0E: "return-void",
    0x0F: "return",
    0x10: "return-wide",
    0x11: "return-object",
    0x12: "const/4",
    0x13: "const/16",
    0x14: "const",
    0x15: "const/high16",
    0x16: "const-wide/16",
    0x17: "const-wide/32",
    0x18: "const-wide",
    0x19: "const-wide/high16",
    0x1A: "const-string",
    0x1B: "const-string/jumbo",
    0x1C: "const-class",
    0x1D: "monitor-enter",
    0x1E: "monitor-exit",
    0x1F: "check-cast",
    0x20: "instance-of",
    0x21: "array-length",
    0x22: "new-instance",
    0x23: "new-array",
    0x24: "filled-new-array",
    0x25: "filled-new-array/range",
    0x26: "fill-array-data",
    0x27: "throw",
    0x28: "goto",
    0x29: "goto/16",
    0x2A: "goto/32",
    0x2B: "packed-switch",
    0x2C: "sparse-switch",
    0x2D: "cmpl-float",
    0x2E: "cmpg-float",
    0x2F: "cmpl-double",
    0x30: "cmpg-double",
    0x31: "cmp-long",
    0x32: "if-eq",
    0x33: "if-ne",
    0x34: "if-lt",
    0x35: "if-ge",
    0x36: "if-gt",
    0x37: "if-le",
    0x38: "if-eqz",
    0x39: "if-nez",
    0x3A: "if-ltz",
    0x3B: "if-gez",
    0x3C: "if-gtz",
    0x3D: "if-lez",
    0x44: "aget",
    0x45: "aget-wide",
    0x46: "aget-object",
    0x47: "aget-boolean",
    0x48: "aget-byte",
    0x49: "aget-char",
    0x4A: "aget-short",
    0x4B: "aput",
    0x4C: "aput-wide",
    0x4D: "aput-object",
    0x4E: "aput-boolean",
    0x4F: "aput-byte",
    0x50: "aput-char",
    0x51: "aput-short",
    0x52: "iget",
    0x53: "iget-wide",
    0x54: "iget-object",
    0x55: "iget-boolean",
    0x56: "iget-byte",
    0x57: "iget-char",
    0x58: "iget-short",
    0x59: "iput",
    0x5A: "iput-wide",
    0x5B: "iput-object",
    0x5C: "iput-boolean",
    0x5D: "iput-byte",
    0x5E: "iput-char",
    0x5F: "iput-short",
    0x60: "sget",
    0x61: "sget-wide",
    0x62: "sget-object",
    0x63: "sget-boolean",
    0x64: "sget-byte",
    0x65: "sget-char",
    0x66: "sget-short",
    0x67: "sput",
    0x68: "sput-wide",
    0x69: "sput-object",
    0x6A: "sput-boolean",
    0x6B: "sput-byte",
    0x6C: "sput-char",
    0x6D: "sput-short",
    0x6E: "invoke-virtual",
    0x6F: "invoke-super",
    0x70: "invoke-direct",
    0x71: "invoke-static",
    0x72: "invoke-interface",
    0x74: "invoke-virtual/range",
    0x75: "invoke-super/range",
    0x76: "invoke-direct/range",
    0x77: "invoke-static/range",
    0x78: "invoke-interface/range",
    0x7B: "neg-int",
    0x7C: "not-int",
    0x7D: "neg-long",
    0x7E: "not-long",
    0x7F: "neg-float",
    0x80: "neg-double",
    0x81: "int-to-long",
    0x82: "int-to-float",
    0x83: "int-to-double",
    0x84: "long-to-int",
    0x85: "long-to-float",
    0x86: "long-to-double",
    0x87: "float-to-int",
    0x88: "float-to-long",
    0x89: "float-to-double",
    0x8A: "double-to-int",
    0x8B: "double-to-long",
    0x8C: "double-to-float",
    0x8D: "int-to-byte",
    0x8E: "int-to-char",
    0x8F: "int-to-short",
    0x90: "add-int",
    0x91: "sub-int",
    0x92: "mul-int",
    0x93: "div-int",
    0x94: "rem-int",
    0x95: "and-int",
    0x96: "or-int",
    0x97: "xor-int",
    0x98: "shl-int",
    0x99: "shr-int",
    0x9A: "ushr-int",
    0x9B: "add-long",
    0x9C: "sub-long",
    0x9D: "mul-long",
    0x9E: "div-long",
    0x9F: "rem-long",
    0xA0: "and-long",
    0xA1: "or-long",
    0xA2: "xor-long",
    0xA3: "shl-long",
    0xA4: "shr-long",
    0xA5: "ushr-long",
    0xA6: "add-float",
    0xA7: "sub-float",
    0xA8: "mul-float",
    0xA9: "div-float",
    0xAA: "rem-float",
    0xAB: "add-double",
    0xAC: "sub-double",
    0xAD: "mul-double",
    0xAE: "div-double",
    0xAF: "rem-double",
    0xB0: "add-int/2addr",
    0xB1: "sub-int/2addr",
    0xB2: "mul-int/2addr",
    0xB3: "div-int/2addr",
    0xB4: "rem-int/2addr",
    0xB5: "and-int/2addr",
    0xB6: "or-int/2addr",
    0xB7: "xor-int/2addr",
    0xB8: "shl-int/2addr",
    0xB9: "shr-int/2addr",
    0xBA: "ushr-int/2addr",
    0xBB: "add-long/2addr",
    0xBC: "sub-long/2addr",
    0xBD: "mul-long/2addr",
    0xBE: "div-long/2addr",
    0xBF: "rem-long/2addr",
    0xC0: "and-long/2addr",
    0xC1: "or-long/2addr",
    0xC2: "xor-long/2addr",
    0xC3: "shl-long/2addr",
    0xC4: "shr-long/2addr",
    0xC5: "ushr-long/2addr",
    0xC6: "add-float/2addr",
    0xC7: "sub-float/2addr",
    0xC8: "mul-float/2addr",
    0xC9: "div-float/2addr",
    0xCA: "rem-float/2addr",
    0xCB: "add-double/2addr",
    0xCC: "sub-double/2addr",
    0xCD: "mul-double/2addr",
    0xCE: "div-double/2addr",
    0xCF: "rem-double/2addr",
    0xD0: "add-int/lit16",
    0xD1: "rsub-int",
    0xD2: "mul-int/lit16",
    0xD3: "div-int/lit16",
    0xD4: "rem-int/lit16",
    0xD5: "and-int/lit16",
    0xD6: "or-int/lit16",
    0xD7: "xor-int/lit16",
    0xD8: "add-int/lit8",
    0xD9: "rsub-int/lit8",
    0xDA: "mul-int/lit8",
    0xDB: "div-int/lit8",
    0xDC: "rem-int/lit8",
    0xDD: "and-int/lit8",
    0xDE: "or-int/lit8",
    0xDF: "xor-int/lit8",
    0xE0: "shl-int/lit8",
    0xE1: "shr-int/lit8",
    0xE2: "ushr-int/lit8",
    0xFA: "invoke-polymorphic",
    0xFB: "invoke-polymorphic/range",
    0xFC: "invoke-custom",
    0xFD: "invoke-custom/range",
    0xFE: "const-method-handle",
    0xFF: "const-method-type",
}

# Instruction length in 16-bit code units
OP_UNITS: dict[int, int] = {
    0x00: 1,
    0x01: 1,
    0x02: 2,
    0x03: 3,
    0x04: 1,
    0x05: 2,
    0x06: 3,
    0x07: 1,
    0x08: 2,
    0x09: 3,
    0x0A: 1,
    0x0B: 1,
    0x0C: 1,
    0x0D: 1,
    0x0E: 1,
    0x0F: 1,
    0x10: 1,
    0x11: 1,
    0x12: 1,
    0x13: 2,
    0x14: 3,
    0x15: 2,
    0x16: 2,
    0x17: 3,
    0x18: 5,
    0x19: 2,
    0x1A: 2,
    0x1B: 3,
    0x1C: 2,
    0x1D: 1,
    0x1E: 1,
    0x1F: 2,
    0x20: 2,
    0x21: 1,
    0x22: 2,
    0x23: 2,
    0x24: 3,
    0x25: 3,
    0x26: 3,
    0x27: 1,
    0x28: 1,
    0x29: 2,
    0x2A: 3,
    0x2B: 3,
    0x2C: 3,
    0x2D: 2,
    0x2E: 2,
    0x2F: 2,
    0x30: 2,
    0x31: 2,
    0x32: 2,
    0x33: 2,
    0x34: 2,
    0x35: 2,
    0x36: 2,
    0x37: 2,
    0x38: 2,
    0x39: 2,
    0x3A: 2,
    0x3B: 2,
    0x3C: 2,
    0x3D: 2,
    0x44: 2,
    0x45: 2,
    0x46: 2,
    0x47: 2,
    0x48: 2,
    0x49: 2,
    0x4A: 2,
    0x4B: 2,
    0x4C: 2,
    0x4D: 2,
    0x4E: 2,
    0x4F: 2,
    0x50: 2,
    0x51: 2,
    0x52: 2,
    0x53: 2,
    0x54: 2,
    0x55: 2,
    0x56: 2,
    0x57: 2,
    0x58: 2,
    0x59: 2,
    0x5A: 2,
    0x5B: 2,
    0x5C: 2,
    0x5D: 2,
    0x5E: 2,
    0x5F: 2,
    0x60: 2,
    0x61: 2,
    0x62: 2,
    0x63: 2,
    0x64: 2,
    0x65: 2,
    0x66: 2,
    0x67: 2,
    0x68: 2,
    0x69: 2,
    0x6A: 2,
    0x6B: 2,
    0x6C: 2,
    0x6D: 2,
    0x6E: 3,
    0x6F: 3,
    0x70: 3,
    0x71: 3,
    0x72: 3,
    0x74: 3,
    0x75: 3,
    0x76: 3,
    0x77: 3,
    0x78: 3,
    0x7B: 1,
    0x7C: 1,
    0x7D: 1,
    0x7E: 1,
    0x7F: 1,
    0x80: 1,
    0x81: 1,
    0x82: 1,
    0x83: 1,
    0x84: 1,
    0x85: 1,
    0x86: 1,
    0x87: 1,
    0x88: 1,
    0x89: 1,
    0x8A: 1,
    0x8B: 1,
    0x8C: 1,
    0x8D: 1,
    0x8E: 1,
    0x8F: 1,
    0x90: 2,
    0x91: 2,
    0x92: 2,
    0x93: 2,
    0x94: 2,
    0x95: 2,
    0x96: 2,
    0x97: 2,
    0x98: 2,
    0x99: 2,
    0x9A: 2,
    0x9B: 2,
    0x9C: 2,
    0x9D: 2,
    0x9E: 2,
    0x9F: 2,
    0xA0: 2,
    0xA1: 2,
    0xA2: 2,
    0xA3: 2,
    0xA4: 2,
    0xA5: 2,
    0xA6: 2,
    0xA7: 2,
    0xA8: 2,
    0xA9: 2,
    0xAA: 2,
    0xAB: 2,
    0xAC: 2,
    0xAD: 2,
    0xAE: 2,
    0xAF: 2,
    0xB0: 1,
    0xB1: 1,
    0xB2: 1,
    0xB3: 1,
    0xB4: 1,
    0xB5: 1,
    0xB6: 1,
    0xB7: 1,
    0xB8: 1,
    0xB9: 1,
    0xBA: 1,
    0xBB: 1,
    0xBC: 1,
    0xBD: 1,
    0xBE: 1,
    0xBF: 1,
    0xC0: 1,
    0xC1: 1,
    0xC2: 1,
    0xC3: 1,
    0xC4: 1,
    0xC5: 1,
    0xC6: 1,
    0xC7: 1,
    0xC8: 1,
    0xC9: 1,
    0xCA: 1,
    0xCB: 1,
    0xCC: 1,
    0xCD: 1,
    0xCE: 1,
    0xCF: 1,
    0xD0: 2,
    0xD1: 2,
    0xD2: 2,
    0xD3: 2,
    0xD4: 2,
    0xD5: 2,
    0xD6: 2,
    0xD7: 2,
    0xD8: 2,
    0xD9: 2,
    0xDA: 2,
    0xDB: 2,
    0xDC: 2,
    0xDD: 2,
    0xDE: 2,
    0xDF: 2,
    0xE0: 2,
    0xE1: 2,
    0xE2: 2,
    0xFA: 4,
    0xFB: 4,
    0xFC: 3,
    0xFD: 3,
    0xFE: 2,
    0xFF: 2,
}

IF_TEST: set[int] = set(range(0x32, 0x38))  # 22t: 2 registers + int16
IF_TESTZ: set[int] = set(range(0x38, 0x3E))  # 21t: 1 register + int16
MOVE_RESULT_OPS: set[int] = {0x0A, 0x0B, 0x0C}
PRODUCER_OPS: set[int] = (
    set(range(0x6E, 0x73))  # invoke-virtual..invoke-interface
    | set(range(0x74, 0x79))  # invoke-virtual/range..invoke-interface/range
    | {0xFA, 0xFB, 0xFC, 0xFD}  # invoke-polymorphic, invoke-custom (+ range)
    | {0x24, 0x25}  # filled-new-array, filled-new-array/range
)
INVOKE_OPS: tuple[int, ...] = (
    tuple(range(0x6E, 0x73)) + tuple(range(0x74, 0x79)) + (0xFA, 0xFB, 0xFC, 0xFD)
)
BRANCH_OPS: set[int] = (
    {0x28, 0x29, 0x2A}  # goto, goto/16, goto/32
    | IF_TEST  # if-eq..if-le
    | IF_TESTZ  # if-eqz..if-lez
    | {0x2B, 0x2C}  # packed-switch, sparse-switch
)
RETURN_OPS: tuple[int, ...] = (0x0E, 0x0F, 0x10, 0x11)
IFIELD_OPS: tuple[int, ...] = tuple(range(0x52, 0x59))
PFIELD_OPS: tuple[int, ...] = tuple(range(0x59, 0x60))
SFIELD_OPS: tuple[int, ...] = tuple(range(0x60, 0x6E))


def u16(b: bytes | bytearray, o: int) -> int:
    return b[o] | (b[o + 1] << 8)


def u32(b: bytes | bytearray, o: int) -> int:
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)


def s16(v: int) -> int:
    return v - 0x10000 if v > 0x7FFF else v


def s32(v: int) -> int:
    return v - 0x100000000 if v > 0x7FFFFFFF else v


def read_uleb128(b: bytes | bytearray, o: int) -> tuple[int, int]:
    """Decode ULEB128 at offset o -> (value, new_offset)."""
    result = 0
    shift = 0
    while True:
        x = b[o]
        o += 1
        result |= (x & 0x7F) << shift
        if (x & 0x80) == 0:
            break
        shift += 7
    return result, o


def insn_units(op: int, data: bytes | bytearray, pos: int, end: int) -> int:
    """Return instruction length in 16-bit code units."""
    if op == 0x00:
        # Pseudo-instructions (switch and array data payloads)
        if pos + 8 <= end:
            ident = data[pos + 1] & 0xFF
            if ident == 0x01:  # packed-switch-payload
                return 4 + u16(data, pos + 2) * 2
            if ident == 0x02:  # sparse-switch-payload
                return 2 + u16(data, pos + 2) * 4
            if ident == 0x03:  # fill-array-data-payload
                width = u16(data, pos + 2)
                count = u32(data, pos + 4)
                return 4 + (count * width + 1) // 2
        return 1
    return OP_UNITS.get(op, 1)


class Dex:
    """Structural reader for Dalvik Executable (DEX) files."""

    def __init__(self, data: bytes | bytearray, name: str = "classes.dex"):
        self.d = bytes(data)
        self.name = name
        self.header: dict[str, int] = {k: u32(self.d, v) for k, v in HEADER_OFFSETS.items()}

    def check(self) -> list[str]:
        """Return a list of structural problems, empty if parses cleanly."""
        problems: list[str] = []
        size = len(self.d)
        if self.d[:4] != b"dex\n":
            problems.append(f"Not a valid DEX: magic={self.d[:4]!r}")
        for key in (
            "string_ids_off",
            "type_ids_off",
            "proto_ids_off",
            "field_ids_off",
            "method_ids_off",
            "class_defs_off",
        ):
            off = self.header[key]
            if not (0 < off < size):
                problems.append(f"{key}=0x{off:x} out of range (file size {size})")
        declared = self.header["file_size"]
        if declared and declared != size:
            problems.append(f"Header file_size={declared} but actual={size}")
        return problems

    # ---- String Table ----
    def read_all_strings(self) -> list[tuple[int, bytes]]:
        """Return list of (string_data_offset, raw_string_bytes) from string_ids."""
        off = self.header["string_ids_off"]
        size = self.header["string_ids_size"]
        out: list[tuple[int, bytes]] = []
        for i in range(size):
            sdata_off = u32(self.d, off + i * 4)
            n, p = read_uleb128(self.d, sdata_off)
            out.append((sdata_off, self.d[p : p + n]))
        return out

    def string(self, idx: int) -> str:
        """Decode string by string_id index."""
        p = u32(self.d, self.header["string_ids_off"] + idx * 4)
        _utf16_len, p = read_uleb128(self.d, p)
        end = self.d.index(b"\x00", p)
        return self.d[p:end].decode("utf-8", "replace")

    def string_safe(self, idx: int) -> str:
        """Safe string decoder that never raises."""
        try:
            if idx >= self.header["string_ids_size"]:
                return f"<string_idx {idx} out of range>"
            return self.string(idx)
        except Exception:
            return f"<string_idx {idx} unreadable>"

    # ---- Type / Proto / Field / Method Tables ----
    def type_(self, idx: int) -> str:
        return self.string(u32(self.d, self.header["type_ids_off"] + idx * 4))

    def proto(self, idx: int) -> str:
        o = self.header["proto_ids_off"] + idx * 12
        ret = self.type_(u32(self.d, o + 4))
        poff = u32(self.d, o + 8)
        params: list[str] = []
        if poff:
            n = u32(self.d, poff)
            p = poff + 4
            for _ in range(n):
                params.append(self.type_(u16(self.d, p)))
                p += 2
        return "(" + "".join(params) + ")" + ret

    def field(self, idx: int) -> tuple[str, str, str]:
        """Return (class_type, name, type)."""
        o = self.header["field_ids_off"] + idx * 8
        return (
            self.type_(u16(self.d, o)),
            self.string(u32(self.d, o + 4)),
            self.type_(u16(self.d, o + 2)),
        )

    def method(self, idx: int) -> tuple[str, str, str]:
        """Return (class_type, name, proto_descriptor)."""
        o = self.header["method_ids_off"] + idx * 8
        return (
            self.type_(u16(self.d, o)),
            self.string(u32(self.d, o + 4)),
            self.proto(u16(self.d, o + 2)),
        )

    # ---- Class Definitions ----
    def class_names(self) -> Iterator[str]:
        for i in range(self.header["class_defs_size"]):
            o = self.header["class_defs_off"] + i * 32
            yield self.type_(u32(self.d, o))

    def find_class(self, fqcn: str) -> int | None:
        """Return class_def_item offset for FQCN descriptor (e.g. 'Lcom/foo/Bar;')."""
        for i in range(self.header["class_defs_size"]):
            o = self.header["class_defs_off"] + i * 32
            if self.type_(u32(self.d, o)) == fqcn:
                return o
        return None

    def methods_at(self, class_def_off: int) -> Iterator[tuple[str, int, str, str, str, int]]:
        """Yield (section, method_idx, class, name, descriptor, code_off)."""
        p = u32(self.d, class_def_off + 24)
        if p == 0:
            return
        sf, p = read_uleb128(self.d, p)
        inf, p = read_uleb128(self.d, p)
        dm, p = read_uleb128(self.d, p)
        vm, p = read_uleb128(self.d, p)
        for _ in range(sf + inf):  # skip fields
            _i, p = read_uleb128(self.d, p)
            _a, p = read_uleb128(self.d, p)
        for section, count in (("direct", dm), ("virtual", vm)):
            running = 0
            for _ in range(count):
                diff, p = read_uleb128(self.d, p)
                running += diff  # delta encoded
                _acc, p = read_uleb128(self.d, p)
                code_off, p = read_uleb128(self.d, p)
                cls, name, desc = self.method(running)
                yield section, running, cls, name, desc, code_off

    def methods_of(self, fqcn: str) -> Iterator[tuple[str, int, str, str, str, int]]:
        cd = self.find_class(fqcn)
        if cd is None:
            raise KeyError(f"Class not found: {fqcn}")
        yield from self.methods_at(cd)

    def find_method(self, fqcn: str, name: str, desc: str) -> tuple[str, int, int] | None:
        """Return (section, method_idx, code_off) or None."""
        for section, idx, _cls, nm, ds, code_off in self.methods_of(fqcn):
            if nm == name and ds == desc:
                return section, idx, code_off
        return None

    # ---- Bytecode Decoding ----
    def code_info(self, code_off: int) -> dict[str, int]:
        return {
            "registers": u16(self.d, code_off),
            "ins": u16(self.d, code_off + 2),
            "outs": u16(self.d, code_off + 4),
            "tries": u16(self.d, code_off + 6),
            "debug_info_off": u32(self.d, code_off + 8),
            "insns_size": u32(self.d, code_off + 12),
            "insns_off": code_off + 16,
        }

    def decode(self, code_off: int) -> Iterator[dict[str, Any]]:
        """Yield decoded instruction dicts for one method body."""
        info = self.code_info(code_off)
        pos = info["insns_off"]
        end = pos + info["insns_size"] * 2
        while pos < end:
            op = self.d[pos]
            units = insn_units(op, self.d, pos, end)
            if units < 1 or pos + units * 2 > end:
                return
            yield {
                "off": pos,
                "op": op,
                "units": units,
                "name": OP_NAMES.get(op, f"op_{op:02x}"),
                "raw": self.d[pos : pos + units * 2],
                "registers": info["registers"],
            }
            pos += units * 2

    def decode_all(self, code_off: int) -> tuple[list[dict[str, Any]], bool, int]:
        """Decode all instructions in method; returns (insns, clean, expected_end)."""
        insns = list(self.decode(code_off))
        info = self.code_info(code_off)
        expected = info["insns_off"] + info["insns_size"] * 2
        ended = insns[-1]["off"] + insns[-1]["units"] * 2 if insns else info["insns_off"]
        return insns, (ended == expected), expected

    def insn_at(self, code_off: int, target_off: int) -> dict[str, Any] | None:
        for insn in self.decode(code_off):
            if insn["off"] == target_off:
                return insn
        return None

    # ---- Control Flow & Branch Target Calculation ----
    def branch_target(self, insn: dict[str, Any]) -> int | None:
        """Absolute target byte offset of branch instruction, or None if not a branch."""
        op, pos, raw = insn["op"], insn["off"], insn["raw"]
        if op in IF_TEST or op in IF_TESTZ:
            return pos + s16(u16(self.d, pos + 2)) * 2
        if op == 0x28:  # goto (10t, signed byte)
            off = raw[1]
            if off > 127:
                off -= 256
            return pos + off * 2
        if op == 0x29:  # goto/16 (20t, signed int16)
            return pos + s16(u16(self.d, pos + 2)) * 2
        if op == 0x2A:  # goto/32 (30t, signed int32)
            off = u32(self.d, pos + 2)
            return pos + s32(off) * 2
        return None

    def switch_targets(self, insn: dict[str, Any]) -> list[int]:
        """Return all absolute destination target byte offsets for a switch instruction."""
        op, pos = insn["op"], insn["off"]
        if op not in (0x2B, 0x2C):
            return []
        payload_rel = s32(u32(self.d, pos + 2))
        payload_off = pos + payload_rel * 2
        if payload_off + 4 > len(self.d):
            return []

        ident = u16(self.d, payload_off)
        size = u16(self.d, payload_off + 2)
        targets: list[int] = []

        if ident == 0x0100:  # packed-switch-payload
            targets_off = payload_off + 8
            for i in range(size):
                tgt_rel = s32(u32(self.d, targets_off + i * 4))
                targets.append(pos + tgt_rel * 2)
        elif ident == 0x0200:  # sparse-switch-payload
            targets_off = payload_off + 4 + size * 4
            for i in range(size):
                tgt_rel = s32(u32(self.d, targets_off + i * 4))
                targets.append(pos + tgt_rel * 2)

        return targets

    def all_targets(self, code_off: int) -> set[int]:
        """Return set of all absolute branch destinations in a method."""
        targets: set[int] = set()
        for insn in self.decode(code_off):
            t = self.branch_target(insn)
            if t is not None:
                targets.add(t)
            for sw_tgt in self.switch_targets(insn):
                targets.add(sw_tgt)
        return targets

    def describe(self, insn: dict[str, Any]) -> str:
        """Format human-readable instruction disassembly string."""
        op, pos, raw = insn["op"], insn["off"], insn["raw"]
        text = f"0x{pos:x}: {raw.hex():<14} {insn['name']}"
        if op in IF_TEST:
            tgt = self.branch_target(insn)
            text += f" v{raw[1] >> 4},v{raw[1] & 0xF} -> 0x{tgt:x}" if tgt is not None else ""
        elif op in IF_TESTZ:
            tgt = self.branch_target(insn)
            text += f" v{raw[1] & 0xF} -> 0x{tgt:x}" if tgt is not None else ""
        elif op in (0x28, 0x29, 0x2A):
            tgt = self.branch_target(insn)
            text += f" -> 0x{tgt:x}" if tgt is not None else ""
        elif op in INVOKE_OPS:
            midx = u16(self.d, pos + 2)
            cls, nm, ds = self.method(midx)
            text += f" {cls}.{nm}{ds}"
        elif op == 0x1A:
            text += f' "{self.string_safe(u16(self.d, pos + 2))}"'
        elif op == 0x1B:
            text += f' "{self.string_safe(u32(self.d, pos + 2))}"'
        return text


def load_dex(source: str | Path | bytes | bytearray, entry: str | None = None) -> tuple[Dex, str]:
    """Load Dex instance from path, ZIP/APK archive member, or raw bytes."""
    if isinstance(source, (bytes, bytearray)):
        return Dex(bytes(source), entry or "<bytes>"), entry or "<bytes>"
    path = str(source)
    if path.lower().endswith((".apk", ".zip", ".jar", ".xapk", ".apks", ".apkm")):
        with zipfile.ZipFile(path) as z:
            if entry:
                return Dex(z.read(entry), entry), entry
            candidates = sorted(
                n for n in z.namelist() if n.startswith("classes") and n.endswith(".dex")
            )
            if not candidates:
                raise ValueError(f"No classes*.dex found in archive: {path}")
            name = candidates[0]
            return Dex(z.read(name), name), name
    with open(path, "rb") as fh:
        return Dex(fh.read(), path), path
