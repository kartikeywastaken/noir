"""APKTool adapter.

Supports both executable and JAR invocation modes.
Manages framework-cache isolation and version compatibility.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from noir.domain.config import NoirConfig
from noir.domain.models import ProcessResult
from noir.infrastructure.processes.runner import run_tool
from noir.infrastructure.tools.apktool import sanitize_host_metadata
from noir.security.xml import fromstring as secure_fromstring


class ApkToolError(Exception):
    """Raised when APKTool operations fail."""

    def __init__(self, message: str, result: ProcessResult | None = None):
        super().__init__(message)
        self.result = result


class ApkToolAdapter:
    """Adapter for APKTool decode and rebuild operations."""

    MIN_SUPPORTED_VERSION = "2.7.0"

    @staticmethod
    def structured_failure(result: ProcessResult | None) -> dict:
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
                    failure = {
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
        else:
            self._version = version_text.splitlines()[0].strip()
        return self._version

    def _check_version(self) -> None:
        """Verify APKTool version compatibility."""
        version = self.get_version()
        parts = version.split(".")
        try:
            major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
            min_parts = self.MIN_SUPPORTED_VERSION.split(".")
            min_major, min_minor, min_patch = (
                int(min_parts[0]),
                int(min_parts[1]),
                int(min_parts[2]),
            )
            if (major, minor, patch) < (min_major, min_minor, min_patch):
                raise ApkToolError(
                    f"APKTool {version} is below minimum supported version "
                    f"{self.MIN_SUPPORTED_VERSION}. Please upgrade."
                )
        except (ValueError, IndexError):
            pass  # Can't parse version, proceed with warning

    def decode(
        self,
        apk_path: Path,
        output_dir: Path,
        *,
        framework_dir: Path | None = None,
        timeout: int | None = None,
        manifest_only: bool = False,
    ) -> ProcessResult:
        """Decode an APK using APKTool.

        Args:
            apk_path: Path to the APK file.
            output_dir: Directory to write decoded contents.
            framework_dir: Isolated framework cache directory.
            timeout: Process timeout in seconds.

        Returns:
            ProcessResult with decode outcome.

        Raises:
            ApkToolError on decode failure.
        """
        self._check_version()

        cmd = self._build_base_command()
        cmd.extend(["d", str(apk_path)])
        cmd.extend(["-o", str(output_dir)])
        if manifest_only:
            # Apktool validates and repacks the container while all app payload
            # remains opaque. Its text manifest is replaced below by a binary-
            # XML-only decoder because apktool loses unresolved resource refs in
            # this mode. The edited manifest is compiled separately with aapt2.
            cmd.extend(["--only-manifest", "-s", "--no-assets"])

        # Do not use --force against user-controlled paths
        # Use a new output directory instead
        if output_dir.exists():
            if output_dir.is_symlink() or not output_dir.is_dir() or any(output_dir.iterdir()):
                raise ApkToolError("Decode output must be a new or empty managed directory")
            output_dir.rmdir()  # Exact empty managed directory only; never recursive deletion.

        # Isolated framework cache
        if framework_dir:
            framework_dir.mkdir(parents=True, exist_ok=True)
            cmd.extend(["-p", str(framework_dir)])

        result = run_tool(
            cmd,
            timeout=timeout or self.config.process_timeout,
            tool_name="apktool",
            tool_version=self._version or "",
        )

        output = f"{result.stdout}\n{result.stderr}"

        # APKTool 3.x known bug: certain APKs with short hex resource names (e.g. res/9E.xml)
        # trigger a DirectoryException during resource copy. Retry with --keep-broken-res.
        _res_copy_error = re.search(
            r"DirectoryException[\s\S]*?Error copying file|Error copying file[^\r\n]*\.xml",
            output,
            re.IGNORECASE,
        )
        if result.exit_code != 0 and _res_copy_error and "--keep-broken-res" not in cmd:
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            retry_cmd = cmd + ["--keep-broken-res"]
            result = run_tool(
                retry_cmd,
                timeout=timeout or self.config.process_timeout,
                tool_name="apktool",
                tool_version=self._version or "",
            )
            output = f"{result.stdout}\n{result.stderr}"

        # Only scan for fatal warning lines when the process actually failed.
        # APKTool 3.x routinely logs "Exception in thread" warnings even on
        # successful --keep-broken-res decodes; treating them as critical on
        # exit_code=0 would incorrectly abort an otherwise-good decode.
        critical_warning = None
        if result.exit_code != 0:
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

        sanitize_host_metadata(output_dir)
        if manifest_only:
            self._decode_binary_manifest(apk_path, output_dir / "AndroidManifest.xml")
        # Always repair <uses-sdk> — APKTool 3.x with --keep-broken-res drops it in
        # both full decode AND manifest-only mode, causing targetSdkVersion=0.
        self._repair_uses_sdk(output_dir)

        return result

    @staticmethod
    def _repair_uses_sdk(decoded_dir: Path) -> None:
        """Inject <uses-sdk> back into AndroidManifest.xml if APKTool dropped it.

        This is a known APKTool 3.x side-effect when --keep-broken-res is used:
        the <uses-sdk> element is omitted, causing targetSdkVersion=0 in the
        rebuilt APK, which Android refuses to install.
        """

        manifest_path = decoded_dir / "AndroidManifest.xml"
        apktool_yml = decoded_dir / "apktool.yml"
        if not manifest_path.exists() or not apktool_yml.exists():
            return

        # Parse SDK versions from apktool.yml (simple line scan — no yaml dep needed)
        min_sdk: str | None = None
        target_sdk: str | None = None
        for line in apktool_yml.read_text(encoding="utf-8").splitlines():
            if "minSdkVersion" in line:
                m = re.search(r"minSdkVersion[:\s]+['\"]?(\d+)", line)
                if m:
                    min_sdk = m.group(1)
            elif "targetSdkVersion" in line:
                m = re.search(r"targetSdkVersion[:\s]+['\"]?(\d+)", line)
                if m:
                    target_sdk = m.group(1)

        if not target_sdk:
            return  # Nothing to repair

        # Check if <uses-sdk> already present
        content = manifest_path.read_text(encoding="utf-8")
        if "uses-sdk" in content:
            return  # Already present — nothing to do

        # Build the <uses-sdk> element and inject after the opening <manifest ...> tag
        attrs = []
        if min_sdk:
            attrs.append(f'android:minSdkVersion="{min_sdk}"')
        if target_sdk:
            attrs.append(f'android:targetSdkVersion="{target_sdk}"')
        uses_sdk_line = "    <uses-sdk " + " ".join(attrs) + " />\n"

        # Find the end of the opening <manifest ...> tag (may span multiple lines)
        insert_after = re.search(r"<manifest\b[^>]*>", content, re.DOTALL)
        if not insert_after:
            return
        pos = insert_after.end()
        repaired = content[:pos] + "\n" + uses_sdk_line + content[pos:]
        manifest_path.write_text(repaired, encoding="utf-8")

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
            apktool_root = secure_fromstring(output_manifest.read_bytes())
            binary_root = secure_fromstring(decoded)
            resources = ARSCParser(resource_table)

            def merge_references(apktool_node: ET.Element, binary_node: ET.Element) -> None:
                if apktool_node.tag != binary_node.tag:
                    raise ValueError("Manifest element mismatch during reference recovery")
                for attribute, current in apktool_node.attrib.items():
                    alternate = binary_node.get(attribute, "")
                    framework_reference = re.fullmatch(r"([@?])android:([0-9A-Fa-f]{8})", alternate)
                    reference = re.fullmatch(r"([@?])([0-9A-Fa-f]{8})", alternate)
                    if framework_reference and current.startswith(framework_reference.group(1)):
                        prefix = framework_reference.group(1)
                        if not current.startswith(f"{prefix}android:"):
                            apktool_node.set(
                                attribute,
                                f"{prefix}android:{current[1:]}",
                            )
                        continue
                    if not reference:
                        continue
                    prefix, encoded_id = reference.groups()
                    resource_id = int(encoded_id, 16)
                    if (resource_id >> 24) == 0x01 and current.startswith(prefix):
                        if not current.startswith(f"{prefix}android:"):
                            apktool_node.set(
                                attribute,
                                f"{prefix}android:{current[1:]}",
                            )
                    elif current == "":
                        resource_name = resources.get_resource_xml_name(resource_id)
                        if not resource_name:
                            raise ValueError(
                                f"Unable to resolve manifest resource reference {alternate}"
                            )
                        if prefix == "?":
                            resource_name = "?" + resource_name[1:]
                        apktool_node.set(attribute, resource_name)
                binary_by_tag: dict[str, list[ET.Element]] = {}
                for child in binary_node:
                    binary_by_tag.setdefault(child.tag, []).append(child)
                seen: dict[str, int] = {}
                for apktool_child in apktool_node:
                    index = seen.get(apktool_child.tag, 0)
                    candidates = binary_by_tag.get(apktool_child.tag, [])
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

    @staticmethod
    def sanitize_host_metadata(target_dir: Path | str) -> dict[str, int]:
        """Expose sanitize_host_metadata as static method on ApkToolAdapter."""
        return sanitize_host_metadata(target_dir)

    @staticmethod
    def _strip_extended_attributes(output_dir: Path) -> None:
        """Backward-compatible alias for sanitize_host_metadata."""
        sanitize_host_metadata(output_dir)

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
    """Restore opaque APK payload entries from the original archive.

    Manifest-only decoding deliberately leaves assets out of the workspace so
    apktool never has to materialize problematic filenames. The final archive
    receives the exact original data for every opaque entry while retaining any
    newly injected dex files produced by NOIR.
    """

    def is_signature(name: str) -> bool:
        upper = name.upper()
        return upper == "META-INF/MANIFEST.MF" or bool(
            re.fullmatch(r"META-INF/[^/]+\.(?:SF|RSA|DSA|EC)", upper)
        )

    def preserve(name: str) -> bool:
        # The hybrid profile changes only the manifest and may add a new Dex.
        # Carry every other original entry through verbatim, including uncommon
        # root-level metadata that apktool does not recognize. Old JAR signing
        # entries must be discarded because the output receives a fresh signature.
        return name != "AndroidManifest.xml" and not is_signature(name)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{rebuilt_apk.name}.",
        suffix=".preserved",
        dir=rebuilt_apk.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with (
            zipfile.ZipFile(original_apk) as source,
            zipfile.ZipFile(rebuilt_apk) as rebuilt,
            zipfile.ZipFile(temporary, "w", allowZip64=True) as output,
        ):
            preserved_names = {
                item.filename for item in source.infolist() if preserve(item.filename)
            }
            for item in rebuilt.infolist():
                if item.filename not in preserved_names and not is_signature(item.filename):
                    output.writestr(item, rebuilt.read(item.filename))
            for item in source.infolist():
                if item.filename in preserved_names:
                    output.writestr(item, source.read(item.filename))
        os.replace(temporary, rebuilt_apk)
    finally:
        temporary.unlink(missing_ok=True)


def _replace_archive_entry(apk_path: Path, name: str, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{apk_path.name}.", suffix=".entry", dir=apk_path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with (
            zipfile.ZipFile(apk_path) as source,
            zipfile.ZipFile(temporary, "w", allowZip64=True) as output,
        ):
            for item in source.infolist():
                if item.filename != name:
                    output.writestr(item, source.read(item.filename))
            output.writestr(name, content, compress_type=zipfile.ZIP_DEFLATED)
        os.replace(temporary, apk_path)
    finally:
        temporary.unlink(missing_ok=True)
