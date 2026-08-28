"""Run the Dart HTTP client against an isolated real NOIR server and owned APK.

Usage from repository root: backend/.venv/bin/python backend/scripts/flutter_smoke.py
Never loads the user's .env, database, APK projects or signing keys.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import uvicorn

from noir.api.app import create_app
from noir.application.demo import _build_fixture_apk
from noir.domain.config import NoirConfig
from noir.domain.models import ApiToken
from noir.infrastructure.database.repositories import TokenRepository


class SmokeConfig(NoirConfig):
    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    flutter = shutil.which("flutter")
    if not flutter:
        raise RuntimeError("Flutter is not on PATH")
    with tempfile.TemporaryDirectory(prefix="noir-flutter-smoke-") as temporary:
        config = SmokeConfig(
            _env_file=None, data_dir=temporary, ai_provider="none", gemini_api_key=""
        )
        fixture = _build_fixture_apk(config)
        app = create_app(config)
        token = secrets.token_urlsafe(32)
        TokenRepository().create(ApiToken(token_hash=hashlib.sha256(token.encode()).hexdigest()))
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
            worker = threading.Thread(
                target=server.run, kwargs={"sockets": [listener]}, daemon=True
            )
            worker.start()
            try:
                deadline = time.monotonic() + 20
                while not server.started:
                    if not worker.is_alive() or time.monotonic() > deadline:
                        raise RuntimeError("Isolated smoke server failed to start")
                    time.sleep(0.05)
                environment = {
                    **os.environ,
                    "NOIR_SMOKE_URL": f"http://127.0.0.1:{port}",
                    "NOIR_SMOKE_TOKEN": token,
                    "NOIR_SMOKE_APK": str(fixture),
                }
                print(
                    "Testing Dart client against real local API and owned fixture (no AI).",
                    flush=True,
                )
                result = subprocess.run(
                    [flutter, "test", "test/local_backend_smoke_test.dart"],
                    cwd=repo / "frontend",
                    env=environment,
                    timeout=240,
                    check=False,
                )
                return result.returncode
            finally:
                server.should_exit = True
                worker.join(timeout=30)
                if worker.is_alive():
                    raise RuntimeError("Smoke server did not shut down")


if __name__ == "__main__":
    raise SystemExit(main())
