"""EC2 service entrypoint: encrypted systemd credentials, existing NOIR API.

No workflow bypasses. Only provisions an independent cloud test signer and API
token on first startup. The token stays stable over service restarts.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def main() -> None:
    credentials = Path(os.environ["CREDENTIALS_DIRECTORY"])
    gemini_discovery = (credentials / "gemini-discovery-api-key").read_text().strip()
    gemini_generation = (credentials / "gemini-generation-api-key").read_text().strip()
    gemini_pool = (credentials / "gemini-api-keys").read_text().strip()
    openrouter = (credentials / "openrouter-api-key").read_text().strip()
    password = (credentials / "signing-password").read_text().strip()
    token = (credentials / "api-token").read_text().strip()
    if (
        not gemini_discovery
        or not gemini_generation
        or not gemini_pool
        or not openrouter
        or not password
        or not token
    ):
        raise RuntimeError("Missing service credentials")
    os.environ["GEMINI_API_KEY_1"] = gemini_discovery
    os.environ["GEMINI_API_KEY_2"] = gemini_generation
    os.environ["NOIR_GEMINI_API_KEYS"] = gemini_pool
    os.environ["NOIR_OPENROUTER_API_KEY"] = openrouter
    os.environ["NOIR_KEYSTORE_PASSWORD"] = password

    from noir.application.signing_service import SigningService
    from noir.domain.config import get_config
    from noir.domain.models import ApiToken
    from noir.infrastructure.database.engine import TokenRow, get_session, init_db
    from noir.infrastructure.database.repositories import TokenRepository
    from noir.infrastructure.processes.runner import run_tool

    config = get_config()
    config.ensure_directories()
    init_db(config.effective_database_url)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with get_session() as session:
        provisioned = session.query(TokenRow).filter_by(token_hash=token_hash).first() is not None
    if not provisioned:
        TokenRepository().create(ApiToken(token_hash=token_hash))

    signer = SigningService(config)
    name = "cloud-test"
    keystore = config.keys_dir / f"{name}.jks"
    if signer.get_profile(name) is None:
        if not keystore.exists():
            result = run_tool(
                [
                    "keytool",
                    "-genkeypair",
                    "-alias",
                    name,
                    "-keyalg",
                    "RSA",
                    "-keysize",
                    "2048",
                    "-validity",
                    "10000",
                    "-keystore",
                    str(keystore),
                    "-storepass:env",
                    "NOIR_SIGN_STORE_PASS",
                    "-keypass:env",
                    "NOIR_SIGN_STORE_PASS",
                    "-dname",
                    "CN=NOIR Cloud Test, O=NOIR, C=XX",
                ],
                timeout=45,
                tool_name="keytool",
                env={"NOIR_SIGN_STORE_PASS": password},
            )
            if result.exit_code:
                raise RuntimeError("Cloud test signing-key generation failed")
            keystore.chmod(0o600)
        # An explicitly supplied keystore uses the existing supported environment
        # password path, not an insecure keyring fallback. Its password is encrypted
        # on disk and supplied in memory by systemd at service startup.
        signer._get_cert_fingerprint(str(keystore), name, password)
        signer.add_user_profile(name, str(keystore), name)

    if os.environ.get("NOIR_DEPLOY_VERIFY_ONLY") == "1":
        return
    import uvicorn

    from noir.api.app import create_app

    uvicorn.run(
        create_app(config),
        host="127.0.0.1",
        port=8787,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        access_log=False,
    )


if __name__ == "__main__":
    main()
