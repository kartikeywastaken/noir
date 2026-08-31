"""Version-gated IL2CPP metadata correlation.

IL2CPP metadata does not itself contain native file offsets. This first bounded
implementation correlates metadata names with unambiguous, sized ELF symbols.
Stripped binaries are refused rather than guessed. That is deliberately narrower
than heuristically locating Unity code-registration structures.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noir.infrastructure.native.adapter import NativePatchError, inspect_elf

IL2CPP_MAGIC = 0xFAB11BAF
SUPPORTED_METADATA_VERSIONS = {24, 27, 29, 31}


class Il2CppMetadataError(Exception):
    """Raised when metadata cannot be correlated without guessing."""


@dataclass(frozen=True)
class Il2CppMethodRef:
    type_full_name: str
    method_signature: str
    symbol_name: str
    file_offset: int
    virtual_address: int
    size: int
    abi: str
    metadata_version: int


class Il2CppMetadata:
    """Read a global-metadata header and resolve symbol-rich IL2CPP methods."""

    def __init__(self, metadata_path: Path, binary_path: Path):
        self.metadata_path = metadata_path
        self.binary_path = binary_path
        self._data = self._read_metadata(metadata_path)
        self.version, self._strings = self._parse_header_and_strings(self._data)
        try:
            self._elf = inspect_elf(binary_path)
        except NativePatchError as exc:
            raise Il2CppMetadataError(str(exc)) from exc

    @staticmethod
    def _read_metadata(path: Path) -> bytes:
        if not path.is_file():
            raise Il2CppMetadataError("global-metadata.dat was not found")
        data = path.read_bytes()
        if len(data) < 32:
            raise Il2CppMetadataError("IL2CPP metadata header is truncated")
        return data

    @staticmethod
    def _parse_header_and_strings(data: bytes) -> tuple[int, set[str]]:
        magic, version = struct.unpack_from("<II", data, 0)
        if magic != IL2CPP_MAGIC:
            raise Il2CppMetadataError("Unrecognized IL2CPP metadata magic")
        if version not in SUPPORTED_METADATA_VERSIONS:
            raise Il2CppMetadataError(
                f"Unsupported IL2CPP metadata version {version}; supported versions are "
                f"{', '.join(map(str, sorted(SUPPORTED_METADATA_VERSIONS)))}"
            )
        string_offset, string_count = struct.unpack_from("<II", data, 24)
        if (
            string_offset < 32
            or string_count <= 0
            or string_offset > len(data)
            or string_offset + string_count > len(data)
        ):
            raise Il2CppMetadataError("IL2CPP metadata string table is outside the file")
        strings = {
            chunk.decode("utf-8", errors="strict")
            for chunk in data[string_offset : string_offset + string_count].split(b"\0")
            if chunk
        }
        return version, strings

    @staticmethod
    def _tokens(value: str) -> list[str]:
        return [token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9]*", value)]

    def find_method(self, type_full_name: str, method_signature: str) -> Il2CppMethodRef:
        """Resolve one method only when metadata and a sized ELF symbol agree."""
        method_name = method_signature.split("(", 1)[0].split()[-1]
        type_name = type_full_name.rsplit(".", 1)[-1]
        metadata_tokens = {
            token
            for value in self._strings
            for token in self._tokens(value)
        }
        requested_tokens = set(self._tokens(f"{type_full_name} {method_signature}"))
        if (
            type_name not in self._strings
            or method_name not in self._strings
            or not requested_tokens <= metadata_tokens
        ):
            raise Il2CppMetadataError(
                "Requested type or method signature is not present in the metadata string table"
            )
        required = {type_name.lower(), method_name.lower()}
        candidates = []
        for symbol in self._elf["exports"]:
            tokens = set(self._tokens(symbol))
            if required <= tokens:
                candidates.append(symbol)
        if len(candidates) != 1:
            reason = "not present" if not candidates else f"ambiguous ({len(candidates)} symbols)"
            raise Il2CppMetadataError(
                "IL2CPP method-to-offset correlation is "
                f"{reason}. Stripped or ambiguous binaries are unsupported; NOIR will not guess."
            )

        return self._resolve_symbol(candidates[0], type_full_name, method_signature)

    def _resolve_symbol(
        self, symbol_name: str, type_full_name: str, method_signature: str
    ) -> Il2CppMethodRef:
        try:
            import lief
        except ImportError as exc:
            raise Il2CppMetadataError("IL2CPP inspection requires LIEF") from exc
        binary: Any = lief.parse(str(self.binary_path))
        if binary is None:
            raise Il2CppMetadataError("IL2CPP library is not a valid ELF binary")
        matches = {
            (int(symbol.value), int(symbol.size)): symbol
            for symbol in binary.symbols
            if symbol.name == symbol_name and int(symbol.value) > 0
        }
        if len(matches) != 1:
            raise Il2CppMetadataError("Resolved IL2CPP symbol has no unambiguous bounded size")
        symbol = next(iter(matches.values()))
        if int(symbol.size) <= 0:
            raise Il2CppMetadataError("Resolved IL2CPP symbol has no unambiguous bounded size")
        address = int(symbol.value)
        for segment in binary.segments:
            start = int(segment.virtual_address)
            stop = start + int(segment.physical_size)
            if start <= address < stop:
                file_offset = int(segment.file_offset) + address - start
                return Il2CppMethodRef(
                    type_full_name=type_full_name,
                    method_signature=method_signature,
                    symbol_name=symbol_name,
                    file_offset=file_offset,
                    virtual_address=address,
                    size=int(symbol.size),
                    abi=self._elf["abi"],
                    metadata_version=self.version,
                )
        raise Il2CppMetadataError("Resolved IL2CPP symbol is not mapped to file-backed bytes")

    def inspect_method(self, type_full_name: str, method_signature: str) -> dict:
        return self.find_method(type_full_name, method_signature).__dict__.copy()

    def search_strings(self, query: str, *, limit: int = 128) -> dict:
        """Return a small, query-ranked metadata vocabulary without raw file data."""
        tokens = set(self._tokens(query))
        candidates = [
            value
            for value in self._strings
            if len(value.encode("utf-8")) <= 512
            and (not tokens or any(token in value.lower() for token in tokens))
        ]
        candidates.sort(
            key=lambda value: (
                -sum(token in value.lower() for token in tokens),
                value.lower(),
            )
        )
        selected = []
        used = 0
        for value in candidates[:limit]:
            size = len(value.encode("utf-8"))
            if used + size > 8_192:
                break
            selected.append(value)
            used += size
        return {
            "metadata_version": self.version,
            "matching_strings": selected,
            "truncated": len(selected) < len(candidates),
        }
