"""APKTool adapter and host metadata defense.

Supports both executable and JAR invocation modes.
Manages framework-cache isolation, host metadata defense (AppleDouble / xattr stripping),
and bounded compiler-feedback diagnostic parsing.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from noir.domain.config import NoirConfig
from noir.domain.models import ProcessResult
from noir.infrastructure.processes.runner import run_tool
from noir.security.xml import fromstring as secure_fromstring


class ApkToolError(Exception):
    """Raised when APKTool operations fail."""

    def __init__(self, message: str, result: ProcessResult | None = None):
        super().__init__(message)
        self.result = result


def sanitize_host_metadata(target_dir: Path | str) -> dict[str, int]:
    """Recursively purge host-specific filesystem pollution before build/decode.

    Purges .DS_Store files, AppleDouble sidecars (._*), and removes all extended
    attributes (com.apple.* and all removable xattrs) to guarantee clean AAPT2
    and Java NIO operations without Illegal byte sequence errors.

    Args:
        target_dir: Path to directory or file to sanitize.

    Returns:
        Dictionary of counts: {"ds_store": N, "apple_double": N, "xattrs": N}
    """
    path_obj = Path(target_dir)
    stats = {"ds_store": 0, "apple_double": 0, "xattrs": 0}

    if not path_obj.exists():
        return stats

    # If it's a single file
    if path_obj.is_file():
        if path_obj.name == ".DS_Store" and not path_obj.is_symlink():
            try:
                path_obj.unlink(missing_ok=True)
                stats["ds_store"] += 1
            except OSError:
                pass
            return stats
        if path_obj.name.startswith("._") and not path_obj.is_symlink():
            try:
                path_obj.unlink(missing_ok=True)
                stats["apple_double"] += 1
            except OSError:
                pass
            return stats

        if hasattr(os, "listxattr") and hasattr(os, "removexattr") and not path_obj.is_symlink():
            try:
                for attr in os.listxattr(path_obj):
                    try:
                        os.removexattr(path_obj, attr)
                        stats["xattrs"] += 1
                    except OSError:
                        pass
            except OSError:
                pass
        return stats

    # If it's a directory
    if path_obj.is_dir():
        # 1. Recursively delete .DS_Store files
        for ds_store in list(path_obj.rglob(".DS_Store")):
            if not ds_store.is_symlink():
                try:
                    ds_store.unlink(missing_ok=True)
                    stats["ds_store"] += 1
                except OSError:
                    pass

        # 2. Recursively delete AppleDouble companion files (._*)
        for appledouble in list(path_obj.rglob("._*")):
            if not appledouble.is_symlink():
                try:
                    appledouble.unlink(missing_ok=True)
                    stats["apple_double"] += 1
                except OSError:
                    pass

        # 3. Strip extended attributes (com.apple.* and all removable xattrs)
        if hasattr(os, "listxattr") and hasattr(os, "removexattr"):
            for item in (path_obj, *path_obj.rglob("*")):
                if item.is_symlink():
                    continue
                try:
                    attrs = os.listxattr(item)
                except OSError:
                    continue
                for attr in attrs:
                    try:
                        os.removexattr(item, attr)
                        stats["xattrs"] += 1
                    except OSError:
                        pass

    return stats


class ApkToolAdapter:
    """Adapter for APKTool decode and rebuild operations."""

    MIN_SUPPORTED_VERSION = "2.7.0"

    @staticmethod
    def sanitize_host_metadata(target_dir: Path | str) -> dict[str, int]:
        """Expose sanitize_host_metadata as static method on ApkToolAdapter."""
        return sanitize_host_metadata(target_dir)

    @staticmethod
    def _strip_extended_attributes(output_dir: Path) -> None:
        """Backward-compatible alias for sanitize_host_metadata."""
        sanitize_host_metadata(output_dir)

    @staticmethod
    def structured_failure(result: ProcessResult | None) -> dict[str, Any]:
        if result is None:
            return {}
        output = f"{result.stderr}\n{result.stdout}"
        patterns = (
            re.compile(
                r"(?P<file>[^\s\[]+\.smali)\[(?P<line>\d+),(?P<column>\d+)\]\s*(?P<message>.+)"
            ),
            re.compile(r"(?P<file>[^:\n]+\.xml):(?P<line>\d+):(?P<column>\d+):?\s*(?P<message>.+)"),
            re.compile(r"(?P<file>[^:\n]+\.xml):(?P<line>\d+):\s*(?P<message>.+)"),
        )
        for raw_line in output.splitlines():
            for pattern in patterns:
                match = pattern.search(raw_line)
                if match:
                    details = match.groupdict()
                    failure: dict[str, Any] = {
                        "stage": (
                            "manifest_compile" if result.tool_name == "aapt2" else "apktool_build"
                        ),
                        "file": details["file"],
                        "line": int(details["line"]),
                        "diagnostic": details["message"].strip(),
                    }
                    if details.get("column"):
                        failure["column"] = int(details["column"])
                    return failure
        last = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "")
        return {
            "stage": "manifest_compile" if result.tool_name == "aapt2" else "apktool_build",
            "diagnostic": last[:1000],
        }

    def __init__(self, config: NoirConfig):
        self.config = config
        self._version: str | None = None

    def _build_base_command(self) -> list[str]:
        """Build the base APKTool command."""
        if self.config.apktool_jar and Path(self.config.apktool_jar).exists():
            return [self.config.java_executable, "-jar", self.config.apktool_jar]
        apktool = shutil.which(self.config.apktool_path)
        if apktool:
            return [apktool]
        raise ApkToolError(
            "APKTool not found. Install with 'brew install apktool' "
            "or set NOIR_APKTOOL_JAR to the path of apktool.jar"
        )

    def get_version(self) -> str:
        """Get the APKTool version."""
        if self._version:
            return self._version

        cmd = self._build_base_command() + ["--version"]
        result = run_tool(cmd, timeout=15, tool_name="apktool")
        if result.exit_code != 0:
            raise ApkToolError("Failed to get APKTool version", result)

        version_text = result.stdout.strip()
        # APKTool outputs version like "2.9.3" or "2.10.0-dirty"
        match = re.match(r"(\d+\.\d+\.\d+)", version_text)
        if match:
            self._version = match.group(1)
            return self._version

        return version_text

    def _check_version(self) -> None:
        """Verify APKTool meets minimum version requirement."""
        version = self.get_version()
        # Parse major.minor.patch
        match = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
        if not match:
            return  # Can't parse, allow it through

        v_tuple = tuple(int(x) for x in match.groups())
        min_tuple = tuple(int(x) for x in self.MIN_SUPPORTED_VERSION.split("."))
        if v_tuple < min_tuple:
            raise ApkToolError(
                f"APKTool version {version} is below minimum supported {self.MIN_SUPPORTED_VERSION}"
            )

    def decode(
        self,
        apk_path: Path,
        output_dir: Path,
        *,
        framework_dir: Path | None = None,
        manifest_only: bool = False,
        timeout: int | None = None,
    ) -> ProcessResult:
        """Decode an APK using APKTool.

        Args:
            apk_path: Path to the input APK.
            output_dir: Destination directory for decoded sources.
            framework_dir: Isolated framework cache directory.
            manifest_only: Only decode AndroidManifest.xml (skip resources and smali).
            timeout: Process timeout in seconds.

        Returns:
            ProcessResult with stdout, stderr, exit code.

        Raises:
            ApkToolError on decode failure.
        """
        self._check_version()

        output_dir.mkdir(parents=True, exist_ok=True)

        cmd = self._build_base_command()
        cmd.extend(["d", str(apk_path), "-o", str(output_dir), "-f"])

        if manifest_only:
            # Fast decode: only the manifest, skip everything else
            cmd.extend(["--only-manifest", "-s", "--no-assets"])

        if framework_dir:
            cmd.extend(["-p", str(framework_dir)])

        result = run_tool(
            cmd,
            timeout=timeout or self.config.process_timeout,
            tool_name="apktool",
            tool_version=self._version or "",
        )

        output = f"{result.stdout}\n{result.stderr}"
        critical_warning = next(
            (
                line.strip()
                for line in output.splitlines()
                if re.search(
                    r"(?i)(illegal byte sequence|could not decode file|malformed (?:xml|resource)|"
                    r"exception in thread|outofmemoryerror|brut\.common\.brutexception)",
                    line,
                )
            ),
            None,
        )
        if result.exit_code != 0 or critical_warning:
            error_msg = f"APKTool decode failed (exit {result.exit_code})"
            stderr = result.stderr.strip()
            if "Could not decode" in stderr:
                error_msg += ": decode error"
            if "framework" in stderr.lower():
                error_msg += (
                    ". Missing vendor framework — install the required framework "
                    "with 'apktool if framework.apk'"
                )
            if critical_warning:
                error_msg += f": {critical_warning[:300]}"
            raise ApkToolError(error_msg, result)

        # Host metadata sanitization immediately after decode
        sanitize_host_metadata(output_dir)

        if manifest_only:
            self._decode_binary_manifest(apk_path, output_dir / "AndroidManifest.xml")

        return result

    @staticmethod
    def _decode_binary_manifest(apk_path: Path, output_manifest: Path) -> None:
        logger_to_restore = None
        previous_level = None
        try:
            from axml.arsc import ARSCParser
            from axml.axml import AXMLPrinter
            from axml.helper.logging import LOGGER as AXML_LOGGER

            with zipfile.ZipFile(apk_path) as archive:
                binary_manifest = archive.read("AndroidManifest.xml")
                resource_table = archive.read("resources.arsc")
            logger_to_restore = AXML_LOGGER
            previous_level = AXML_LOGGER.level
            AXML_LOGGER.setLevel("CRITICAL")
            printer = AXMLPrinter(binary_manifest)
            decoded = printer.get_xml()
            if not decoded or b"<manifest" not in decoded:
                raise ValueError("AXML decoder returned no Android manifest")
            binary_root = secure_fromstring(decoded.decode("utf-8", errors="replace"))
            apktool_root = secure_fromstring(output_manifest.read_text(encoding="utf-8"))
            arsc = ARSCParser(resource_table)

            def resolve_package_name(resource_id: int) -> str | None:
                package_id = (resource_id >> 24) & 0xFF
                for _package in arsc.get_packages_names():
                    try:
                        resolved = arsc.get_package_name(package_id)
                        if resolved:
                            return resolved
                    except (KeyError, ValueError, IndexError, AttributeError):
                        pass
                packages = arsc.get_packages_names()
                return packages[0] if packages else None

            def format_resource_reference(resource_id: int) -> str | None:
                try:
                    resolved = arsc.analyze_resource(resource_id)
                except Exception:
                    resolved = None
                if not resolved:
                    return None
                type_name, entry_name = resolved
                package_name = resolve_package_name(resource_id)
                if package_name:
                    return f"@{package_name}:{type_name}/{entry_name}"
                return f"@{type_name}/{entry_name}"

            def merge_references(apktool_elem: ET.Element, binary_elem: ET.Element) -> None:
                for key, value in list(apktool_elem.attrib.items()):
                    if key.startswith("{http://schemas.android.com/apk/res/android}resid"):
                        continue
                    if not value.startswith("@null"):
                        continue
                    binary_value = binary_elem.attrib.get(key)
                    if not binary_value or not binary_value.startswith("@"):
                        continue
                    clean_id = binary_value[1:]
                    if clean_id.startswith("0x") or clean_id.startswith("0X"):
                        try:
                            parsed_id = int(clean_id, 16)
                        except ValueError:
                            parsed_id = None
                    elif clean_id.isdigit():
                        parsed_id = int(clean_id)
                    else:
                        parsed_id = None
                    if parsed_id is None:
                        continue
                    recovered = format_resource_reference(parsed_id)
                    if recovered:
                        apktool_elem.set(key, recovered)

                seen: dict[str, int] = {}
                for apktool_child in list(apktool_elem):
                    index = seen.get(apktool_child.tag, 0)
                    candidates = [
                        candidate for candidate in binary_elem if candidate.tag == apktool_child.tag
                    ]
                    android_name = apktool_child.get(
                        "{http://schemas.android.com/apk/res/android}name"
                    )
                    named_candidates = (
                        [
                            candidate
                            for candidate in candidates
                            if candidate.get("{http://schemas.android.com/apk/res/android}name")
                            == android_name
                        ]
                        if android_name
                        else []
                    )
                    if len(named_candidates) == 1:
                        selected = named_candidates[0]
                    elif index < len(candidates):
                        selected = candidates[index]
                    else:
                        raise ValueError("Manifest structure mismatch during reference recovery")
                    seen[apktool_child.tag] = index + 1
                    merge_references(apktool_child, selected)

            merge_references(apktool_root, binary_root)
            ET.indent(apktool_root, space="    ")
            output_manifest.write_bytes(
                ET.tostring(apktool_root, encoding="utf-8", xml_declaration=True)
            )
        except Exception as exc:
            raise ApkToolError(f"Binary Android manifest decode failed: {exc}") from exc
        finally:
            if logger_to_restore is not None and previous_level is not None:
                logger_to_restore.setLevel(previous_level)

    def compile_manifest(
        self,
        original_apk: Path,
        decoded_manifest: Path,
        rebuilt_apk: Path,
    ) -> None:
        """Compile one edited manifest and insert it into an apktool rebuild."""
        # Sanitize manifest directory before compiling
        sanitize_host_metadata(decoded_manifest.parent)

        aapt2 = self.config.resolve_tool_path("aapt2")
        sdk = Path(self.config.android_sdk_dir) if self.config.android_sdk_dir else None
        platform_jars = list((sdk / "platforms").glob("android-*/android.jar")) if sdk else []
        platform_jars.sort(
            key=lambda path: (
                int(path.parent.name.removeprefix("android-"))
                if path.parent.name.removeprefix("android-").isdigit()
                else -1
            )
        )
        if not platform_jars:
            raise ApkToolError("Android platform android.jar is required to compile the manifest")

        with tempfile.TemporaryDirectory(prefix="noir-manifest-") as temporary_name:
            compiled_apk = Path(temporary_name) / "compiled-manifest.apk"
            result = run_tool(
                [
                    aapt2,
                    "link",
                    "-o",
                    str(compiled_apk),
                    "-I",
                    str(platform_jars[-1]),
                    "-I",
                    str(original_apk),
                    "--manifest",
                    str(decoded_manifest),
                ],
                timeout=120,
                tool_name="aapt2",
            )
            if result.exit_code != 0 or not compiled_apk.is_file():
                diagnostic = (result.stderr or result.stdout).strip()
                raise ApkToolError(f"Manifest compilation failed: {diagnostic[:2000]}", result)
            with zipfile.ZipFile(compiled_apk) as archive:
                compiled_manifest = archive.read("AndroidManifest.xml")
            _replace_archive_entry(rebuilt_apk, "AndroidManifest.xml", compiled_manifest)

    def build(
        self,
        decoded_dir: Path,
        output_apk: Path,
        *,
        framework_dir: Path | None = None,
        timeout: int | None = None,
    ) -> ProcessResult:
        """Rebuild an APK from decoded directory.

        Args:
            decoded_dir: Path to the decoded workspace.
            output_apk: Path for the output unsigned APK.
            framework_dir: Isolated framework cache directory.
            timeout: Process timeout in seconds.

        Returns:
            ProcessResult with build outcome.

        Raises:
            ApkToolError on build failure.
        """
        # Crucial: Sanitize host metadata immediately before apktool build
        sanitize_host_metadata(decoded_dir)

        self._check_version()

        cmd = self._build_base_command()
        cmd.extend(["b", str(decoded_dir)])
        cmd.extend(["-o", str(output_apk)])

        if framework_dir:
            cmd.extend(["-p", str(framework_dir)])

        result = run_tool(
            cmd,
            timeout=timeout or self.config.process_timeout,
            tool_name="apktool",
            tool_version=self._version or "",
        )

        if result.exit_code != 0:
            failure = self.structured_failure(result)
            location = ""
            if failure.get("file"):
                location = (
                    f" at {failure['file']}:{failure.get('line', '?')}:{failure.get('column', '?')}"
                )
            diagnostic = failure.get("diagnostic")
            suffix = f": {diagnostic}" if diagnostic else ""
            raise ApkToolError(
                f"APKTool build failed (exit {result.exit_code}){location}{suffix}",
                result,
            )

        # Verify output exists and is non-empty
        if not output_apk.exists() or output_apk.stat().st_size == 0:
            raise ApkToolError("APKTool build produced no output", result)

        return result


def overlay_preserved_entries(original_apk: Path, rebuilt_apk: Path) -> None:
    """Restore opaque APK payload entries from the original archive."""
    with tempfile.TemporaryDirectory(prefix="noir-overlay-") as scratch_dir:
        temp_rebuilt = Path(scratch_dir) / "rebuilt.apk"
        shutil.copy2(rebuilt_apk, temp_rebuilt)
        rebuilt_entries: set[str] = set()

        with zipfile.ZipFile(temp_rebuilt) as rebuild_zip:
            rebuilt_entries = set(rebuild_zip.namelist())

        with (
            zipfile.ZipFile(temp_rebuilt, mode="a") as target_zip,
            zipfile.ZipFile(original_apk, mode="r") as source_zip,
        ):
            for item in source_zip.infolist():
                name = item.filename
                if name.startswith("META-INF/"):
                    continue
                if name == "AndroidManifest.xml":
                    continue
                if name in rebuilt_entries:
                    continue
                data = source_zip.read(name)
                info = zipfile.ZipInfo(filename=name, date_time=item.date_time)
                info.compress_type = item.compress_type
                info.comment = item.comment
                info.extra = item.extra
                target_zip.writestr(info, data)
        shutil.move(temp_rebuilt, rebuilt_apk)


def _replace_archive_entry(apk_path: Path, target_name: str, replacement_bytes: bytes) -> None:
    with tempfile.TemporaryDirectory(prefix="noir-replace-entry-") as scratch_dir:
        temp_apk = Path(scratch_dir) / "patched.apk"
        with (
            zipfile.ZipFile(apk_path, mode="r") as source_zip,
            zipfile.ZipFile(temp_apk, mode="w") as dest_zip,
        ):
            for item in source_zip.infolist():
                if item.filename == target_name:
                    continue
                dest_zip.writestr(item, source_zip.read(item.filename))
            dest_zip.writestr(target_name, replacement_bytes)
        shutil.move(temp_apk, apk_path)
