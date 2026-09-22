"""APK input validation.

Validates APK files before processing, treating them as untrusted input.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Any

from noir.domain.config import NoirConfig


class ApkValidationError(Exception):
    """Raised when APK validation fails."""

    def __init__(self, message: str, code: str = "invalid_apk"):
        super().__init__(message)
        self.code = code


def validate_apk(
    apk_path: str | Path,
    config: NoirConfig | None = None,
    *,
    require_valid_dex_headers: bool = False,
) -> dict:
    """Validate an APK file for processing.

    Returns a dict with validation results and metadata.
    Raises ApkValidationError on hard failures.
    """
    from noir.domain.config import get_config

    if config is None:
        config = get_config()

    path = Path(apk_path)
    result: dict = {
        "valid": False,
        "warnings": [],
        "path": str(path),
        "size": 0,
        "entry_count": 0,
        "has_manifest": False,
        "has_dex": False,
        "classification": "unknown",
        "dex_verification": {},
    }

    # File existence and readability
    if not path.exists():
        raise ApkValidationError(f"File not found: {path}", "file_not_found")
    if not path.is_file():
        raise ApkValidationError(f"Not a regular file: {path}", "not_a_file")
    if not os.access(path, os.R_OK):
        raise ApkValidationError(f"File not readable: {path}", "not_readable")

    # Extension check (case-insensitive)
    if path.suffix.lower() != ".apk":
        raise ApkValidationError(f"Expected .apk extension, got '{path.suffix}'", "wrong_extension")

    # Size check
    file_size = path.stat().st_size
    result["size"] = file_size
    if file_size == 0:
        raise ApkValidationError("APK file is empty", "empty_file")
    if file_size > config.max_apk_size:
        raise ApkValidationError(
            f"APK file too large: {file_size} bytes (max {config.max_apk_size})",
            "file_too_large",
        )

    # ZIP container check
    if not zipfile.is_zipfile(path):
        raise ApkValidationError("File is not a valid ZIP container", "not_zip")

    try:
        with zipfile.ZipFile(path, "r") as zf:
            entries = zf.infolist()
            result["entry_count"] = len(entries)

            # Archive entry limit
            if len(entries) > config.max_archive_entries:
                raise ApkValidationError(
                    f"Too many archive entries: {len(entries)} (max {config.max_archive_entries})",
                    "too_many_entries",
                )

            # Check for dangerous entries
            total_expanded = 0
            total_compressed = 0
            for entry in entries:
                name = entry.filename

                # Absolute paths
                if name.startswith("/") or name.startswith("\\"):
                    raise ApkValidationError(
                        f"Archive contains absolute path: {name}", "absolute_path"
                    )

                # Path traversal
                if ".." in name.split("/") or ".." in name.split("\\"):
                    raise ApkValidationError(
                        f"Archive contains path traversal: {name}", "path_traversal"
                    )

                # Symlinks (ZIP external attrs)
                if entry.external_attr >> 16 & 0o120000 == 0o120000:
                    raise ApkValidationError(f"Archive contains symlink: {name}", "symlink_entry")

                # Platform-specific unsafe names
                unsafe_names = {"CON", "PRN", "AUX", "NUL"}
                base_name = Path(name).stem.upper()
                if base_name in unsafe_names:
                    result["warnings"].append(f"Potentially unsafe filename: {name}")

                # Size tracking
                total_expanded += entry.file_size
                total_compressed += entry.compress_size

            # Total expanded size
            if total_expanded > config.max_expanded_size:
                raise ApkValidationError(
                    f"Total expanded size too large: {total_expanded} bytes",
                    "expanded_too_large",
                )

            # Detect archive bombs using aggregate expansion. APKs produced by
            # Unity and similar engines can legitimately contain an individual
            # highly-compressible split asset; rejecting one small entry by its
            # local ratio causes false positives. Aggregate expansion plus the
            # absolute expanded-size and entry-count caps above preserves the
            # safety boundary without rejecting those APKs.
            if total_expanded >= config.compression_ratio_min_expanded_size:
                archive_ratio = total_expanded / max(total_compressed, 1)
                if archive_ratio > config.max_compression_ratio:
                    raise ApkValidationError(
                        f"Suspicious archive compression ratio ({archive_ratio:.0f}:1)",
                        "compression_bomb",
                    )

            # Check for AndroidManifest.xml
            entry_names = {e.filename for e in entries}
            result["has_manifest"] = "AndroidManifest.xml" in entry_names
            if not result["has_manifest"]:
                raise ApkValidationError("APK does not contain AndroidManifest.xml", "no_manifest")

            # Check for classes*.dex and verify DEX headers
            from noir.infrastructure.dex.integrity import verify_dex_header

            dex_entries = [
                e for e in entries if e.filename.startswith("classes") and e.filename.endswith(".dex")
            ]
            result["has_dex"] = len(dex_entries) > 0
            dex_verification: dict[str, dict[str, Any]] = {}
            for de in dex_entries:
                raw_dex = zf.read(de.filename)
                ok, msg = verify_dex_header(raw_dex)
                dex_verification[de.filename] = {"valid": ok, "message": msg}
                if not ok:
                    result["warnings"].append(
                        f"DEX header verification failed for '{de.filename}': {msg}"
                    )
                    if require_valid_dex_headers:
                        raise ApkValidationError(
                            f"Corrupted DEX header in '{de.filename}': {msg}",
                            "corrupted_dex_header",
                        )
            result["dex_verification"] = dex_verification

            # Classify
            if result["has_dex"]:
                result["classification"] = "standard_apk"
            else:
                result["classification"] = "resource_only_apk"
                result["warnings"].append(
                    "No classes.dex found — this may be a resource-only package"
                )

            # Check for encrypted entries
            for entry in entries:
                if entry.flag_bits & 0x1:
                    raise ApkValidationError(
                        f"Archive contains encrypted entry: {entry.filename}",
                        "encrypted_entry",
                    )

            # Check for duplicate paths (case-insensitive collision)
            lower_names: dict[str, str] = {}
            for name in entry_names:
                lower = name.lower()
                if lower in lower_names and lower_names[lower] != name:
                    result["warnings"].append(
                        f"Case-folding collision: '{name}' vs '{lower_names[lower]}'"
                    )
                lower_names[lower] = name

    except zipfile.BadZipFile as e:
        raise ApkValidationError(f"Malformed ZIP archive: {e}", "bad_zip") from None

    result["valid"] = True
    return result


def validate_apk_dex_headers(apk_path: str | Path) -> dict[str, tuple[bool, str]]:
    """Container-level verification of all DEX headers in an APK archive.

    Returns a dict mapping entry name (e.g. 'classes.dex') to (is_valid, message).
    """
    from noir.infrastructure.dex.integrity import verify_dex_header

    path = Path(apk_path)
    if not path.is_file():
        raise ApkValidationError(f"File not found: {path}", "file_not_found")
    if not zipfile.is_zipfile(path):
        raise ApkValidationError(f"Not a valid ZIP container: {path}", "not_zip")

    results: dict[str, tuple[bool, str]] = {}
    with zipfile.ZipFile(path, "r") as zf:
        for entry in zf.infolist():
            if entry.filename.startswith("classes") and entry.filename.endswith(".dex"):
                raw = zf.read(entry.filename)
                results[entry.filename] = verify_dex_header(raw)
    return results
