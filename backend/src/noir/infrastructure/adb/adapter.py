"""ADB adapter and device service for device operations."""

from __future__ import annotations

import re
from pathlib import Path

from noir.domain.config import NoirConfig, get_config
from noir.domain.models import ProcessResult
from noir.infrastructure.processes.runner import run_tool


class AdbError(Exception):
    pass


class AdbAdapter:
    """Adapter for ADB device operations."""

    def __init__(self, config: NoirConfig | None = None):
        self.config = config or get_config()
        self._adb_path = self.config.resolve_tool_path("adb")

    def _run(self, args: list[str], serial: str | None = None, **kwargs) -> ProcessResult:
        cmd = [self._adb_path]
        if serial:
            cmd.extend(["-s", serial])
        cmd.extend(args)
        return run_tool(cmd, timeout=kwargs.get("timeout", 60), tool_name="adb")

    def list_devices(self) -> list[dict]:
        """List connected devices."""
        result = self._run(["devices", "-l"])
        if result.exit_code != 0:
            raise AdbError(f"adb devices failed: {result.stderr}")

        devices = []
        for line in result.stdout.splitlines()[1:]:
            line = line.strip()
            if not line or "offline" in line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                serial = parts[0]
                status = parts[1]
                info = " ".join(parts[2:]) if len(parts) > 2 else ""
                devices.append(
                    {
                        "serial": serial,
                        "status": status,
                        "info": info,
                    }
                )
        return devices

    def list_packages(self, serial: str) -> list[str]:
        """List installed packages on a device."""
        result = self._run(["shell", "pm", "list", "packages"], serial=serial)
        if result.exit_code != 0:
            raise AdbError(f"Failed to list packages: {result.stderr}")

        packages = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("package:"):
                packages.append(line[8:])
        return sorted(packages)

    def get_package_path(self, serial: str, package: str) -> list[str]:
        """Get APK paths for a package."""
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", package):
            raise AdbError("Invalid Android package name")
        result = self._run(["shell", "pm", "path", package], serial=serial)
        if result.exit_code != 0:
            raise AdbError(f"Failed to get package path: {result.stderr}")

        paths = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("package:"):
                paths.append(line[8:])
        return paths

    def pull_file(self, serial: str, remote_path: str, local_path: Path) -> None:
        """Pull a file from device."""
        local_path.parent.mkdir(parents=True, exist_ok=True)
        result = self._run(
            ["pull", remote_path, str(local_path)],
            serial=serial,
            timeout=120,
        )
        if result.exit_code != 0:
            raise AdbError(f"Failed to pull {remote_path}: {result.stderr}")

    def install(self, serial: str, apk_path: Path, *, replace: bool = False) -> dict:
        """Install an APK on device."""
        args = ["install"]
        if replace:
            args.append("-r")
        args.append(str(apk_path))

        result = self._run(args, serial=serial, timeout=120)
        success = result.exit_code == 0 and "Success" in result.stdout

        return {
            "success": success,
            "output": result.stdout + result.stderr,
            "exit_code": result.exit_code,
        }

    def launch_activity(self, serial: str, component: str) -> dict:
        """Launch a specific activity."""
        result = self._run(
            ["shell", "am", "start", "-n", component],
            serial=serial,
        )
        return {
            "success": result.exit_code == 0,
            "output": result.stdout + result.stderr,
        }

    def capture_logs(self, serial: str, *, package: str | None = None, timeout: int = 10) -> str:
        """Capture bounded logcat output."""
        args = ["logcat", "-d", "-t", "100"]
        if package:
            args.extend(["--pid", self._get_pid(serial, package) or "0"])

        result = self._run(args, serial=serial, timeout=timeout)
        return result.stdout[:50_000]

    def _get_pid(self, serial: str, package: str) -> str | None:
        """Get PID of a running package."""
        result = self._run(["shell", "pidof", package], serial=serial, timeout=5)
        pid = result.stdout.strip()
        return pid if pid.isdigit() else None


class DeviceService:
    """Higher-level device operations."""

    def __init__(self, config: NoirConfig | None = None):
        self.config = config or get_config()
        self.adb = AdbAdapter(config)

    def list_devices(self) -> list[dict]:
        return self.adb.list_devices()

    def list_packages(self, serial: str) -> list[str]:
        return self.adb.list_packages(serial)

    def import_from_device(self, package: str, serial: str, output_dir: Path) -> dict:
        """Pull APK(s) from a device.

        For split packages, returns all APKs with an explicit unsupported-for-repackaging note.
        """
        paths = self.adb.get_package_path(serial, package)
        if not paths:
            raise AdbError(f"Package not found or not accessible: {package}")

        output_dir.mkdir(parents=True, exist_ok=True)
        pulled_files: list[str] = []

        for remote_path in paths:
            filename = Path(remote_path).name
            local_path = output_dir / filename
            self.adb.pull_file(serial, remote_path, local_path)
            pulled_files.append(str(local_path))

        is_split = len(pulled_files) > 1

        result = {
            "package": package,
            "serial": serial,
            "files": pulled_files,
            "is_split": is_split,
        }

        if is_split:
            result["warning"] = (
                "This is a split APK package. Only individual APKs were retrieved. "
                "Repackaging split APKs into a universal APK is NOT supported. "
                "The base.apk alone does NOT represent the complete installed application."
            )
            result["repackaging_supported"] = False
        else:
            result["repackaging_supported"] = True

        return result

    def install_apk(self, serial: str, apk_path: Path, *, replace: bool = False) -> dict:
        """Install an APK on an explicitly selected device."""
        if not apk_path.exists():
            raise AdbError(f"APK not found: {apk_path}")
        return self.adb.install(serial, apk_path, replace=replace)

    def smoke_test(
        self, serial: str, apk_path: Path, package: str, launcher_component: str
    ) -> dict:
        """Run a basic smoke test: install, launch, check for crash."""
        results: dict = {
            "install": None,
            "launch": None,
            "crash_detected": False,
            "logs": "",
        }

        # Install
        install_result = self.adb.install(serial, apk_path, replace=True)
        results["install"] = install_result
        if not install_result["success"]:
            return results

        # Launch
        launch_result = self.adb.launch_activity(serial, launcher_component)
        results["launch"] = launch_result

        # Wait a moment and capture logs
        import time

        time.sleep(3)

        logs = self.adb.capture_logs(serial, package=package, timeout=5)
        results["logs"] = logs

        # Check for crash indicators
        crash_indicators = [
            "FATAL EXCEPTION",
            "Process: " + package + ", PID:",
            "java.lang.RuntimeException",
            "AndroidRuntime",
        ]
        results["crash_detected"] = any(ind in logs for ind in crash_indicators)

        return results
