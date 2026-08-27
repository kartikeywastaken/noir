"""APKTool adapter.

Supports both executable and JAR invocation modes.
Manages framework-cache isolation and version compatibility.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from noir.domain.config import NoirConfig
from noir.domain.models import ProcessResult
from noir.infrastructure.processes.runner import run_tool


class ApkToolError(Exception):
    """Raised when APKTool operations fail."""

    def __init__(self, message: str, result: ProcessResult | None = None):
        super().__init__(message)
        self.result = result


class ApkToolAdapter:
    """Adapter for APKTool decode and rebuild operations."""

    MIN_SUPPORTED_VERSION = "2.7.0"

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

        if result.exit_code != 0:
            error_msg = f"APKTool decode failed (exit {result.exit_code})"
            stderr = result.stderr.strip()
            if "Could not decode" in stderr:
                error_msg += ": decode error"
            if "framework" in stderr.lower():
                error_msg += (
                    ". Missing vendor framework — install the required framework "
                    "with 'apktool if framework.apk'"
                )
            raise ApkToolError(error_msg, result)

        return result

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
            raise ApkToolError(f"APKTool build failed (exit {result.exit_code})", result)

        # Verify output exists and is non-empty
        if not output_apk.exists() or output_apk.stat().st_size == 0:
            raise ApkToolError("APKTool build produced no output", result)

        return result
