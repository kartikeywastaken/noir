"""Smali directory scanner and class/method indexer."""

from __future__ import annotations

import re
from pathlib import Path

from noir.domain.models import SmaliClassInfo

# Regex patterns for Smali parsing
CLASS_PATTERN = re.compile(r"^\.class\s+.*\s+(L[\w/$]+;)")
METHOD_PATTERN = re.compile(r"^\.method\s+.*\s+(\S+\(.*\)\S+)")
END_METHOD_PATTERN = re.compile(r"^\.end method")


def scan_smali_directories(decoded_dir: Path) -> list[str]:
    """Find all smali directories (smali, smali_classes2, etc.)."""
    dirs: list[str] = []
    if not decoded_dir.exists():
        return dirs
    for child in sorted(decoded_dir.iterdir()):
        if child.is_dir() and child.name.startswith("smali"):
            dirs.append(child.name)
    return dirs


def index_smali_classes(
    decoded_dir: Path,
    max_classes: int = 100_000,
) -> list[SmaliClassInfo]:
    """Index all Smali classes and their methods.

    Returns a list of SmaliClassInfo with class descriptors, file paths,
    and method signatures.
    """
    classes: list[SmaliClassInfo] = []
    smali_dirs = scan_smali_directories(decoded_dir)

    for dex_idx, smali_dir_name in enumerate(smali_dirs):
        smali_dir = decoded_dir / smali_dir_name
        if not smali_dir.exists():
            continue

        for smali_file in sorted(smali_dir.rglob("*.smali")):
            if len(classes) >= max_classes:
                break

            try:
                content = smali_file.read_text(errors="replace")
            except OSError:
                continue

            rel_path = str(smali_file.relative_to(decoded_dir))
            descriptor = ""
            methods: list[str] = []

            for line in content.splitlines():
                line = line.strip()

                # Match class descriptor
                class_match = CLASS_PATTERN.match(line)
                if class_match:
                    descriptor = class_match.group(1)
                    continue

                # Match method signature
                method_match = METHOD_PATTERN.match(line)
                if method_match:
                    methods.append(method_match.group(1))

            if descriptor:
                classes.append(
                    SmaliClassInfo(
                        descriptor=descriptor,
                        file_path=rel_path,
                        method_count=len(methods),
                        methods=methods,
                        dex_index=dex_idx,
                    )
                )

    return classes


def find_class(classes: list[SmaliClassInfo], descriptor: str) -> SmaliClassInfo | None:
    """Find a class by its descriptor."""
    for cls in classes:
        if cls.descriptor == descriptor:
            return cls
    return None


def find_method(
    classes: list[SmaliClassInfo], class_descriptor: str, method_signature: str
) -> tuple[SmaliClassInfo | None, str | None]:
    """Find a specific method in a specific class.

    Returns (class_info, method_signature) or (None, None).
    """
    cls = find_class(classes, class_descriptor)
    if cls is None:
        return None, None
    for method in cls.methods:
        if method == method_signature:
            return cls, method
    return cls, None


def detect_obfuscation(classes: list[SmaliClassInfo]) -> list[str]:
    """Heuristic detection of obfuscation indicators.

    NOTE: These are heuristics only. A positive indicator does not prove
    obfuscation, and absence does not prove lack of obfuscation.
    """
    indicators: list[str] = []

    if not classes:
        return indicators

    # Check for very short class names (common in ProGuard/R8 output)
    short_names = sum(1 for c in classes if len(c.descriptor.split("/")[-1].rstrip(";")) <= 2)
    if short_names > len(classes) * 0.3 and len(classes) > 10:
        indicators.append(
            f"Heuristic: {short_names}/{len(classes)} classes have very short names "
            "(common in ProGuard/R8 obfuscated code)"
        )

    # Check for single-letter package segments
    single_letter_pkgs = set()
    for c in classes:
        parts = c.descriptor.lstrip("L").rstrip(";").split("/")
        for part in parts[:-1]:  # Exclude class name
            if len(part) == 1 and part.isalpha():
                single_letter_pkgs.add(part)
    if len(single_letter_pkgs) > 3:
        indicators.append(
            f"Heuristic: Found {len(single_letter_pkgs)} single-letter package segments "
            "(common in obfuscated code)"
        )

    return indicators


def search_smali(
    decoded_dir: Path,
    query: str,
    *,
    max_results: int = 100,
) -> list[dict]:
    """Search for text within Smali files."""
    results: list[dict] = []
    smali_dirs = scan_smali_directories(decoded_dir)

    for smali_dir_name in smali_dirs:
        smali_dir = decoded_dir / smali_dir_name
        for smali_file in sorted(smali_dir.rglob("*.smali")):
            if len(results) >= max_results:
                return results
            try:
                content = smali_file.read_text(errors="replace")
                for line_num, line in enumerate(content.splitlines(), 1):
                    if query in line:
                        results.append(
                            {
                                "file": str(smali_file.relative_to(decoded_dir)),
                                "line": line_num,
                                "content": line.strip()[:500],
                            }
                        )
                        if len(results) >= max_results:
                            return results
            except OSError:
                continue

    return results
