"""Toolchain doctor — checks availability and versions of all required/optional tools."""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

from noir.domain.config import NoirConfig
from noir.domain.models import DoctorReport, ToolCheck
from noir.infrastructure.processes.runner import get_tool_version, run_tool


def _parse_java_major_version(version_str: str) -> int | None:
    match = re.search(r'version\s+"?(\d+)(?:\.(\d+))?', version_str, re.IGNORECASE)
    if not match:
        match = re.search(r"\b(?:openjdk|java)\s+(\d+)(?:\.(\d+))?", version_str, re.IGNORECASE)
    if match:
        major = int(match.group(1))
        if major == 1 and match.group(2):
            return int(match.group(2))
        return major
    return None


def _check_python() -> ToolCheck:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    ok = sys.version_info >= (3, 12)
    return ToolCheck(
        name="Python",
        available=ok,
        path=sys.executable,
        version=version,
        required_for="required_for_import",
        message="OK" if ok else "Python 3.12+ required",
    )


def _check_java(config: NoirConfig) -> ToolCheck:
    java = config.java_executable
    java_home = shutil.which(java)
    if not java_home:
        return ToolCheck(
            name="Java",
            available=False,
            required_for="required_for_import",
            message="Java not found. Install JDK 17+ (JDK 21 recommended).",
        )
    result = run_tool([java, "-version"], timeout=10, tool_name="java")
    version = ""
    for line in (result.stdout + result.stderr).splitlines():
        if "version" in line.lower():
            version = line.strip()
            break
    if not version and (result.stdout or result.stderr):
        version = (result.stdout or result.stderr).splitlines()[0].strip()

    major = _parse_java_major_version(version)
    if major is not None and major < 17:
        return ToolCheck(
            name="Java",
            available=False,
            path=java_home,
            version=version,
            required_for="required_for_import",
            message=f"JDK 17+ required (found Java {major}: {version})",
        )

    return ToolCheck(
        name="Java",
        available=True,
        path=java_home,
        version=version,
        required_for="required_for_import",
        message="OK",
    )


def _check_apktool(config: NoirConfig) -> ToolCheck:
    # Check JAR mode first
    if config.apktool_jar and Path(config.apktool_jar).exists():
        result = run_tool(
            [config.java_executable, "-jar", config.apktool_jar, "--version"],
            timeout=15,
            tool_name="apktool",
        )
        if result.exit_code == 0:
            version = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
            return ToolCheck(
                name="APKTool",
                available=True,
                path=config.apktool_jar,
                version=version,
                required_for="required_for_import",
                message=f"JAR mode: {version}",
            )

    # Check executable mode
    apktool = shutil.which(config.apktool_path)
    if not apktool:
        return ToolCheck(
            name="APKTool",
            available=False,
            required_for="required_for_import",
            message="APKTool not found. Install with: brew install apktool",
        )

    result = run_tool([apktool, "--version"], timeout=15, tool_name="apktool")
    version = ""
    if result.exit_code == 0:
        version = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    return ToolCheck(
        name="APKTool",
        available=True,
        path=apktool,
        version=version,
        required_for="required_for_import",
        message=f"OK: {version}",
    )


def _check_sdk_tool(config: NoirConfig, tool_name: str, required_for: str) -> ToolCheck:
    tool_path = config.resolve_tool_path(tool_name)
    found = shutil.which(tool_path)
    if not found and Path(tool_path).is_absolute() and Path(tool_path).exists():
        found = tool_path

    if not found:
        return ToolCheck(
            name=tool_name,
            available=False,
            required_for=required_for,
            message=f"{tool_name} not found. Set ANDROID_HOME or install Android build-tools.",
        )

    version = get_tool_version(found) or ""
    return ToolCheck(
        name=tool_name,
        available=True,
        path=found,
        version=version,
        required_for=required_for,
        message="OK",
    )


def _check_keytool() -> ToolCheck:
    keytool = shutil.which("keytool")
    if not keytool:
        return ToolCheck(
            name="keytool",
            available=False,
            required_for="required_for_build",
            message="keytool not found. Install a JDK.",
        )
    return ToolCheck(
        name="keytool",
        available=True,
        path=keytool,
        required_for="required_for_build",
        message="OK (from JDK)",
    )


def _check_adb(config: NoirConfig) -> ToolCheck:
    adb_path = config.resolve_tool_path("adb")
    found = shutil.which(adb_path)
    if not found and Path(adb_path).is_absolute() and Path(adb_path).exists():
        found = adb_path
    if not found:
        return ToolCheck(
            name="adb",
            available=False,
            required_for="optional_for_device",
            message="adb not found. Install Android platform-tools for device operations.",
        )
    version = get_tool_version(found) or ""
    return ToolCheck(
        name="adb",
        available=True,
        path=found,
        version=version,
        required_for="optional_for_device",
        message="OK",
    )


def _check_cil_tool(config: NoirConfig) -> ToolCheck:
    from noir.infrastructure.dotnet.adapter import _default_tool_path

    dotnet_path = config.resolve_tool_path("dotnet")
    dotnet = shutil.which(dotnet_path)
    if not dotnet and Path(dotnet_path).is_file():
        dotnet = dotnet_path
    configured = Path(config.noir_cil_tool_path) if config.noir_cil_tool_path else None
    tool = configured or _default_tool_path()
    if not dotnet:
        return ToolCheck(
            name="Mono/CIL patching",
            available=False,
            required_for="optional_for_binary",
            message=".NET 8 runtime not found; install dotnet-sdk-8.0.",
        )
    if not tool.is_file():
        return ToolCheck(
            name="Mono/CIL patching",
            available=False,
            path=str(tool),
            required_for="optional_for_binary",
            message="Bundled noir-cil-tool has not been built.",
        )
    return ToolCheck(
        name="Mono/CIL patching",
        available=True,
        path=str(tool),
        version=get_tool_version(dotnet) or "",
        required_for="optional_for_binary",
        message="dnlib companion ready",
    )


def _check_native_libraries() -> ToolCheck:
    try:
        import capstone  # type: ignore[import-untyped]
        import lief

        from noir.infrastructure.native.adapter import _keystone

        assembler = type(_keystone("arm64-v8a")).__name__
    except (ImportError, OSError, RuntimeError) as exc:
        return ToolCheck(
            name="IL2CPP/native patching",
            available=False,
            required_for="optional_for_binary",
            message=f"Binary libraries unavailable: {exc}",
        )
    return ToolCheck(
        name="IL2CPP/native patching",
        available=True,
        version=(
            f"LIEF {getattr(lief, '__version__', 'unknown')}; "
            f"Capstone {getattr(capstone, '__version__', 'unknown')}"
        ),
        required_for="optional_for_binary",
        message=f"ELF/disassembly ready; assembler={assembler}",
    )


def _check_ai(config: NoirConfig) -> ToolCheck:
    if config.ai_provider == "none":
        return ToolCheck(
            name="AI Provider",
            available=False,
            required_for="optional_for_ai",
            message="AI provider disabled.",
        )
    if config.ai_provider not in {"gemini", "adk"}:
        return ToolCheck(
            name="AI Provider",
            available=False,
            required_for="optional_for_ai",
            message=f"Unsupported AI provider: {config.ai_provider}",
        )
    if not config.gemini_key_for("generation"):
        return ToolCheck(
            name="AI Provider",
            available=False,
            required_for="optional_for_ai",
            message=(
                f"AI provider '{config.ai_provider}' configured but no Gemini key is set. "
                "Configure GEMINI_API_KEY_1 and GEMINI_API_KEY_2, or legacy GEMINI_API_KEY."
            ),
        )
    split = bool(
        config.gemini_discovery_api_key.get_secret_value()
        and config.gemini_generation_api_key.get_secret_value()
    )
    return ToolCheck(
        name="AI Provider",
        available=True,
        required_for="optional_for_ai",
        message=(
            f"Provider: {config.ai_provider}, Model: {config.ai_model} "
            f"({'separate discovery/generation keys' if split else 'shared fallback key'})"
        ),
    )


def _check_data_dir(config: NoirConfig) -> ToolCheck:
    data_dir = Path(config.data_dir)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        test_file = data_dir / ".write_test"
        test_file.write_text("test")
        test_file.unlink()
        return ToolCheck(
            name="Data Directory",
            available=True,
            path=str(data_dir),
            required_for="required_for_import",
            message=f"Writable: {data_dir}",
        )
    except OSError as e:
        return ToolCheck(
            name="Data Directory",
            available=False,
            path=str(data_dir),
            required_for="required_for_import",
            message=f"Not writable: {e}",
        )


def _check_xattr_support() -> ToolCheck:
    """Check Apple metadata / filesystem extended attributes capability."""
    has_py_xattr = hasattr(os, "listxattr")
    cli_path = shutil.which("xattr")
    available = has_py_xattr or bool(cli_path)
    if available:
        details = "os.listxattr" if has_py_xattr else f"xattr CLI ({cli_path})"
        return ToolCheck(
            name="xattr",
            available=True,
            path=cli_path or "builtin",
            required_for="optional_for_environment",
            message=f"Extended attributes (xattr) support active via {details}",
        )
    return ToolCheck(
        name="xattr",
        available=False,
        required_for="optional_for_environment",
        message="Neither os.listxattr nor xattr CLI found",
    )


def run_doctor(config: NoirConfig) -> DoctorReport:
    """Run all toolchain checks and produce a report."""
    checks: list[ToolCheck] = [
        _check_python(),
        _check_data_dir(config),
        _check_java(config),
        _check_apktool(config),
        _check_sdk_tool(config, "zipalign", "required_for_build"),
        _check_sdk_tool(config, "apksigner", "required_for_build"),
        _check_sdk_tool(config, "aapt2", "required_for_build"),
        _check_keytool(),
        _check_xattr_support(),
        _check_cil_tool(config),
        _check_native_libraries(),
        _check_adb(config),
        _check_ai(config),
    ]

    required_import = all(c.available for c in checks if c.required_for == "required_for_import")
    required_build = all(
        c.available
        for c in checks
        if c.required_for in ("required_for_import", "required_for_build")
    )
    ai_ok = any(c.available for c in checks if c.required_for == "optional_for_ai")
    device_ok = any(c.available for c in checks if c.required_for == "optional_for_device")
    binary_checks = [c for c in checks if c.required_for == "optional_for_binary"]

    summary_parts = []
    if required_import:
        summary_parts.append("Import/decode: ready")
    else:
        summary_parts.append("Import/decode: MISSING TOOLS")
    if required_build:
        summary_parts.append("Rebuild/sign: ready")
    else:
        summary_parts.append("Rebuild/sign: MISSING TOOLS")
    summary_parts.append(f"AI: {'configured' if ai_ok else 'not configured'}")
    summary_parts.append(f"Device: {'available' if device_ok else 'not available'}")
    summary_parts.append(
        "Binary patching: "
        + ("ready" if binary_checks and all(c.available for c in binary_checks) else "partial")
    )

    return DoctorReport(
        checks=checks,
        all_required_available=required_import,
        build_capable=required_build,
        ai_configured=ai_ok,
        device_capable=device_ok,
        summary=" | ".join(summary_parts),
    )


class DoctorService:
    """Service to evaluate toolchain status and capability truth."""

    def __init__(self, config: NoirConfig | None = None) -> None:
        from noir.domain.config import get_config

        self.config = config or get_config()

    def run_doctor(self) -> DoctorReport:
        """Run all toolchain checks and produce a report."""
        return run_doctor(self.config)

    def check_capabilities(self) -> DoctorReport:
        """Alias for capability inventory."""
        return self.run_doctor()

    def check_toolchain_for_build(self) -> tuple[bool, list[str]]:
        """Verify toolchain is capable of building and signing APKs."""
        report = self.run_doctor()
        diagnostics: list[str] = []
        for check in report.checks:
            if not check.available and check.required_for in (
                "required_for_import",
                "required_for_build",
            ):
                diagnostics.append(f"{check.name}: {check.message}")
        return report.build_capable, diagnostics
