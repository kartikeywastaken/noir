"""Run once as root after bootstrap. Encrypt credentials and install service files."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


def install(source: Path, destination: str, mode: int = 0o644) -> None:
    shutil.copyfile(source, destination)
    os.chmod(destination, mode)


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("Run as root")
    source = Path(__file__).resolve().parent
    upload = Path("/home/ubuntu/noir-deploy/credentials-upload.json")
    bundle = json.loads(upload.read_text())
    for name in ("gemini-api-key", "api-token", "signing-password"):
        target = Path("/etc/credstore.encrypted") / f"noir-{name}"
        if target.exists():
            # Provisioning must never silently rotate existing service credentials.
            recovered = subprocess.run(
                ["systemd-creds", "decrypt", f"--name={name}", str(target), "-"],
                check=True,
                capture_output=True,
            ).stdout.decode()
            if recovered != bundle[name]:
                raise SystemExit(f"Existing {name} differs; refusing to replace it")
        else:
            subprocess.run(
                ["systemd-creds", "encrypt", "--with-key=host", f"--name={name}", "-", str(target)],
                input=bundle[name].encode(),
                check=True,
                capture_output=True,
            )
            target.chmod(0o600)

    settings = dict(
        line.split("=", 1)
        for line in (source / "backend.env").read_text().splitlines()
        if line and not line.startswith("#")
    )
    allowed = {
        "NOIR_AI_PROVIDER",
        "NOIR_AI_MODEL",
        "NOIR_AI_TIMEOUT",
        "NOIR_AI_RETRY_LIMIT",
        "NOIR_AI_RESPONSE_RETRY_LIMIT",
        "NOIR_AI_MAX_OUTPUT_TOKENS",
        "NOIR_AI_MAX_REQUEST_SIZE",
        "NOIR_AI_MAX_OUTPUT_SIZE",
    }
    for key, value in bundle["settings"].items():
        if key not in allowed or "\n" in str(value) or "\r" in str(value):
            raise SystemExit("Invalid deployment setting")
        settings[key] = str(value)
    environment = Path("/etc/noir/backend.env")
    environment.write_text("".join(f"{key}={value}\n" for key, value in settings.items()))
    environment.chmod(0o600)
    install(source / "service.py", "/opt/noir/deployment/service.py")
    install(source / "java-wrapper", "/opt/noir/deployment/java-wrapper", 0o755)
    install(source / "noir.service", "/etc/systemd/system/noir.service")
    install(source / "Caddyfile", "/etc/caddy/Caddyfile")
    subprocess.run(["systemd-analyze", "verify", "/etc/systemd/system/noir.service"], check=True)
    subprocess.run(["caddy", "validate", "--config", "/etc/caddy/Caddyfile"], check=True)
    upload.unlink()
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "noir.service"], check=True)
    subprocess.run(["systemctl", "enable", "caddy.service"], check=True)
    subprocess.run(["systemctl", "reload-or-restart", "caddy.service"], check=True)
    print("Deployment installed; staged plaintext credentials removed.")


if __name__ == "__main__":
    main()
