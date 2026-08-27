"""Android build tools adapters: zipalign and apksigner."""

from __future__ import annotations

from pathlib import Path

from noir.domain.config import NoirConfig
from noir.infrastructure.processes.runner import run_tool


class ZipalignError(Exception):
    pass


class ApksignerError(Exception):
    pass


def zipalign(
    config: NoirConfig,
    input_apk: Path,
    output_apk: Path,
    *,
    page_align_shared_libs: bool = True,
) -> dict:
    """Align an APK using zipalign.

    Args:
        config: NOIR configuration.
        input_apk: Path to unaligned APK.
        output_apk: Path for aligned output.
        page_align_shared_libs: Enable page alignment for shared libs (16KB pages).

    Returns:
        Dict with alignment result.
    """
    tool_path = config.resolve_tool_path("zipalign")

    args = [tool_path]
    if page_align_shared_libs:
        args.extend(["-P", "16"])
    args.extend(["-f", "4", str(input_apk), str(output_apk)])

    result = run_tool(args, timeout=120, tool_name="zipalign")

    if result.exit_code != 0:
        raise ZipalignError(f"zipalign failed (exit {result.exit_code}): {result.stderr}")

    if not output_apk.exists():
        raise ZipalignError("zipalign produced no output")

    return {
        "aligned_path": str(output_apk),
        "exit_code": result.exit_code,
        "duration": result.duration_seconds,
    }


def verify_alignment(config: NoirConfig, apk_path: Path) -> dict:
    """Verify APK alignment."""
    tool_path = config.resolve_tool_path("zipalign")
    args = [tool_path, "-c", "-P", "16", "4", str(apk_path)]

    result = run_tool(args, timeout=30, tool_name="zipalign")

    return {
        "aligned": result.exit_code == 0,
        "exit_code": result.exit_code,
        "output": result.stdout + result.stderr,
    }


def sign_apk(
    config: NoirConfig,
    apk_path: Path,
    output_path: Path,
    *,
    keystore_path: str,
    key_alias: str,
    keystore_password: str,
    key_password: str | None = None,
    v1_signing: bool = True,
    v2_signing: bool = True,
    v3_signing: bool = True,
) -> dict:
    """Sign an APK using apksigner.

    Passwords are passed via environment variables, never command-line args.
    """
    tool_path = config.resolve_tool_path("apksigner")

    args = [
        tool_path,
        "sign",
        "--ks",
        keystore_path,
        "--ks-key-alias",
        key_alias,
        "--ks-pass",
        "env:NOIR_SIGN_STORE_PASS",
        "--key-pass",
        "env:NOIR_SIGN_KEY_PASS",
        "--v1-signing-enabled",
        str(v1_signing).lower(),
        "--v2-signing-enabled",
        str(v2_signing).lower(),
        "--v3-signing-enabled",
        str(v3_signing).lower(),
        "--out",
        str(output_path),
        str(apk_path),
    ]
    result = run_tool(
        args,
        timeout=120,
        tool_name="apksigner",
        env={
            "NOIR_SIGN_STORE_PASS": keystore_password,
            "NOIR_SIGN_KEY_PASS": key_password or keystore_password,
        },
    )

    if result.exit_code != 0:
        raise ApksignerError(f"apksigner sign failed (exit {result.exit_code}): {result.stderr}")

    return {
        "signed_path": str(output_path),
        "exit_code": result.exit_code,
        "duration": result.duration_seconds,
    }


def verify_signature(config: NoirConfig, apk_path: Path) -> dict:
    """Verify APK signature using apksigner."""
    tool_path = config.resolve_tool_path("apksigner")
    args = [tool_path, "verify", "--verbose", "--print-certs", str(apk_path)]

    result = run_tool(args, timeout=30, tool_name="apksigner")

    verified = result.exit_code == 0
    output = result.stdout + result.stderr

    # Parse certificate info
    cert_info = {}
    for line in output.splitlines():
        line = line.strip()
        if "SHA-256" in line and "digest" in line.lower():
            cert_info["sha256_digest"] = line.split(":")[-1].strip() if ":" in line else line
        if "Signer #" in line:
            cert_info["signer"] = line
        if line.startswith("Verified using"):
            cert_info["schemes"] = line

    return {
        "verified": verified,
        "exit_code": result.exit_code,
        "output": output,
        "cert_info": cert_info,
    }
