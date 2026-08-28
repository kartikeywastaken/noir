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
        shutil.copy2("/opt/noir/deployment/service.py", backup / "service.py")
        with tarfile.open(source, "r:gz") as archive:
            # Only extract this application; refuse symlinks and path traversal.
            for member in archive.getmembers():
                if (
                    not member.name.startswith("backend/")
                    or ".." in Path(member.name).parts
                    or member.issym()
                    or member.islnk()
                ):
                    raise RuntimeError("Unexpected deployment archive member")
            archive.extractall("/opt/noir", filter="data")
        shutil.copy2("/opt/noir/backend/deploy/ec2/service.py", "/opt/noir/deployment/service.py")
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
