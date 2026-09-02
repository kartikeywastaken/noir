"""Run locally with the backend venv. Never print secret values."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from noir.domain.config import NoirConfig


def private_json(path: Path, data: dict) -> None:
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


def main() -> None:
    config = NoirConfig()
    gemini_discovery = config.gemini_key_for("discovery")
    gemini_generation = config.gemini_key_for("generation")
    if not gemini_discovery or not gemini_generation:
        raise SystemExit(
            "Gemini credentials are required. Configure GEMINI_API_KEY_1 and "
            "GEMINI_API_KEY_2, or legacy GEMINI_API_KEY."
        )
    directory = Path.home() / ".noir/deployments/ec2-stockholm"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = secrets.token_urlsafe(48)
    private_json(
        directory / "credentials-upload.json",
        {
            "gemini-discovery-api-key": gemini_discovery,
            "gemini-generation-api-key": gemini_generation,
            "api-token": token,
            "signing-password": secrets.token_urlsafe(48),
            "settings": {
                "NOIR_AI_PROVIDER": config.ai_provider,
                "NOIR_AI_MODEL": config.ai_model,
                "NOIR_AI_FALLBACK_MODEL": config.ai_fallback_model,
                "NOIR_AI_TIMEOUT": config.ai_timeout,
                "NOIR_AI_RETRY_LIMIT": config.ai_retry_limit,
                "NOIR_AI_RESPONSE_RETRY_LIMIT": config.ai_response_retry_limit,
                "NOIR_AI_MAX_OUTPUT_TOKENS": config.ai_max_output_tokens,
                "NOIR_AI_MAX_REQUEST_SIZE": config.ai_max_request_size,
                "NOIR_AI_MAX_OUTPUT_SIZE": config.ai_max_output_size,
            },
        },
    )
    private_json(
        directory / "connection.json",
        {
            "backend_url": "https://noir-16-171-197-228.sslip.io",
            "bearer_token": token,
            "signing_profile": "cloud-test",
            "ssh_user": "ubuntu",
            "ssh_host": "16.171.197.228",
            "ssh_key": str(Path.home() / "Desktop" / "noir-server.pem"),
            "note": (
                "Owner workspace access only. Keep this token private; "
                "invite other users separately. "
                "The public IP can change after EC2 stop/start."
            ),
        },
    )
    print("Prepared private deployment credentials and connection.json; no secrets printed.")


if __name__ == "__main__":
    main()
