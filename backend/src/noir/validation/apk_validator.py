"""APK input validation.

Validates APK files before processing, treating them as untrusted input.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

from noir.domain.config import NoirConfig


class ApkValidationError(Exception):
    """Raised when APK validation fails."""

    def __init__(self, message: str, code: str = "invalid_apk"):
        super().__init__(message)
        self.code = code


def validate_apk(apk_path: str | Path, config: NoirConfig | None = None) -> dict:
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

                # Compression ratio check
                if entry.compress_size > 0:
                    ratio = entry.file_size / entry.compress_size
                    if ratio > config.max_compression_ratio:
                        raise ApkValidationError(
                            f"Suspicious compression ratio ({ratio:.0f}:1) for {name}",
                            "compression_bomb",
                        )

            # Total expanded size
            if total_expanded > config.max_expanded_size:
                raise ApkValidationError(
                    f"Total expanded size too large: {total_expanded} bytes",
                    "expanded_too_large",
                )

            # Check for AndroidManifest.xml
            entry_names = {e.filename for e in entries}
            result["has_manifest"] = "AndroidManifest.xml" in entry_names
            if not result["has_manifest"]:
                raise ApkValidationError("APK does not contain AndroidManifest.xml", "no_manifest")

            # Check for classes.dex (not required — resource-only packages exist)
            result["has_dex"] = any(
                n.startswith("classes") and n.endswith(".dex") for n in entry_names
            )

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
