"""Back up and upgrade the existing dedicated EC2 backend. Run as root."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlopen

SECRET_ENVIRONMENT_KEYS = {"NOIR_OPENROUTER_API_KEY"}


def _read_environment(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )


def _write_environment(path: Path, values: dict[str, str]) -> None:
    safe_values = {
        key: value for key, value in values.items() if key not in SECRET_ENVIRONMENT_KEYS
    }
    path.write_text("".join(f"{key}={value}\n" for key, value in safe_values.items()))
    path.chmod(0o600)


def _migrate_openrouter_credential(environment: dict[str, str]) -> None:
    """Move a legacy plaintext OpenRouter key into systemd's encrypted store."""
    target = Path("/etc/credstore.encrypted/noir-openrouter-api-key")
    if target.exists():
        return
    secret = environment.get("NOIR_OPENROUTER_API_KEY", "").strip()
    if not secret:
        raise RuntimeError(
            "OpenRouter discovery requires /etc/credstore.encrypted/"
            "noir-openrouter-api-key before this upgrade"
        )
    temporary = target.with_suffix(".new")
    subprocess.run(
        [
            "systemd-creds",
            "encrypt",
            "--with-key=host",
            "--name=openrouter-api-key",
            "-",
            str(temporary),
        ],
        input=secret.encode(),
        check=True,
        capture_output=True,
    )
    temporary.chmod(0o600)
    temporary.replace(target)


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("Run as root")
    source = Path("/home/ubuntu/noir-deploy/backend-private.tar.gz")
    database = Path("/var/lib/noir/data/noir.db")
    with sqlite3.connect(database) as connection:
        active = connection.execute(
            "SELECT job_id FROM jobs WHERE state IN ('running', 'queued')"
        ).fetchall()
    if active:
        raise SystemExit("Active jobs exist; let them finish before upgrading")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = Path("/var/backups/noir") / stamp
    backup.mkdir(parents=True, mode=0o700)
    backup.parent.chmod(0o700)
    current_environment = _read_environment(Path("/etc/noir/backend.env"))
    _migrate_openrouter_credential(current_environment)
    subprocess.run(["systemctl", "stop", "noir"], check=True)
    try:
        with (
            sqlite3.connect(database) as connection,
            sqlite3.connect(backup / "noir.db") as destination,
        ):
            connection.backup(destination)
        (backup / "noir.db").chmod(0o600)
        shutil.copytree(
            "/opt/noir/backend", backup / "backend", ignore=shutil.ignore_patterns("__pycache__")
        )
        if Path("/opt/noir/tools").is_dir():
            shutil.copytree(
                "/opt/noir/tools",
                backup / "tools",
                ignore=shutil.ignore_patterns("bin", "obj"),
            )
        shutil.copy2("/opt/noir/deployment/service.py", backup / "service.py")
        _write_environment(backup / "backend.env", current_environment)
        shutil.copy2("/etc/systemd/system/noir.service", backup / "noir.service")
        with tarfile.open(source, "r:gz") as archive:
            # Only extract this application; refuse symlinks and path traversal.
            for member in archive.getmembers():
                if (
                    (
                        member.name not in {"backend", "tools"}
                        and not member.name.startswith(("backend/", "tools/"))
                    )
                    or ".." in Path(member.name).parts
                    or member.issym()
                    or member.islnk()
                ):
                    raise RuntimeError("Unexpected deployment archive member")
            archive.extractall("/opt/noir", filter="data")
        # macOS tar archives can carry AppleDouble sidecars as extended metadata.
        # They are never source files and make the Linux C# compiler reject the tree.
        for metadata in Path("/opt/noir").rglob("._*"):
            metadata.unlink()
        if not shutil.which("dotnet"):
            subprocess.run(
                [
                    "apt-get",
                    "-o",
                    "DPkg::Lock::Timeout=180",
                    "install",
                    "-y",
                    "dotnet-sdk-8.0",
                ],
                check=True,
            )
        subprocess.run(
            [
                "dotnet",
                "build",
                "/opt/noir/tools/noir-cil-tool/noir-cil-tool.csproj",
                "--configuration",
                "Release",
            ],
            check=True,
        )
        for retired in (
            "src/noir/infrastructure/ai/errors.py",
            "src/noir/infrastructure/ai/openai.py",
            "src/noir/infrastructure/ai/routing.py",
            "tests/unit/test_openai_routing.py",
        ):
            (Path("/opt/noir/backend") / retired).unlink(missing_ok=True)
        subprocess.run(
            [
                "/opt/noir/venv/bin/python",
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-e",
                "/opt/noir/backend",
            ],
            check=True,
        )
        shutil.copy2("/opt/noir/backend/deploy/ec2/service.py", "/opt/noir/deployment/service.py")
        shutil.copy2(
            "/opt/noir/backend/deploy/ec2/noir.service", "/etc/systemd/system/noir.service"
        )
        desired_environment = _read_environment(
            Path("/opt/noir/backend/deploy/ec2/backend.env")
        )
        for name in (
            "NOIR_AI_PROVIDER",
            "NOIR_AI_MODEL",
            "NOIR_AI_FALLBACK_MODEL",
            "NOIR_GEMINI_API_KEYS",
            "NOIR_DISCOVERY_PROVIDER",
            "NOIR_OPENROUTER_DISCOVERY_MODEL",
            "NOIR_OPENROUTER_DISCOVERY_FALLBACK_MODEL",
            "NOIR_AI_STALL_TIMEOUT",
            "NOIR_DOTNET_TOOL_PATH",
            "NOIR_CIL_TOOL_PATH",
            "NOIR_UPLOAD_CHUNK_SIZE",
            "NOIR_MAX_UPLOAD_CHUNK_SIZE",
            "NOIR_UPLOAD_SESSION_TTL",
            "NOIR_ARTIFACT_STORE",
            "NOIR_S3_BUCKET",
            "NOIR_S3_REGION",
            "NOIR_S3_PREFIX",
            "NOIR_S3_PRESIGN_EXPIRY",
            "NOIR_S3_UPLOAD_PART_SIZE",
        ):
            if name in desired_environment:
                current_environment[name] = desired_environment[name]
        _write_environment(Path("/etc/noir/backend.env"), current_environment)
        subprocess.run(
            ["systemd-analyze", "verify", "/etc/systemd/system/noir.service"], check=True
        )
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "start", "noir"], check=True)
        for _ in range(30):
            try:
                with urlopen("http://127.0.0.1:8787/v1/health", timeout=5) as response:
                    if response.status == 200:
                        print(f"Upgrade healthy. Pre-upgrade database/source backup: {backup}")
                        return
            except OSError:
                time.sleep(1)
        raise RuntimeError("Updated service did not become healthy")
    except Exception:
        # Never silently roll a private-workspace database back to an old server
        # that does not enforce ownership. Leave it stopped for inspection.
        subprocess.run(["systemctl", "stop", "noir"], check=False)
        print(f"Upgrade stopped safely. Backup: {backup}")
        raise


if __name__ == "__main__":
    main()
