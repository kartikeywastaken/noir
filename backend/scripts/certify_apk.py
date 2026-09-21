"""Certify standalone APKs against NOIR's hybrid deterministic pipeline.

Examples:
    backend/.venv/bin/python backend/scripts/certify_apk.py app.apk
    backend/.venv/bin/python backend/scripts/certify_apk.py --matrix app1.apk app2.apk

The command is intentionally local and destructive only inside its temporary
directory. It performs an unchanged manifest round-trip, applies deterministic
operations, rebuilds, restores opaque archive entries, aligns, signs with an
ephemeral key, and verifies the resulting signature.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import zipfile
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

from noir.application.deterministic_service import (
    OperationSpec,
    create_deterministic_patch,
    create_deterministic_plan,
)
from noir.domain.config import NoirConfig
from noir.infrastructure.android_tools.tools import (
    sign_apk,
    verify_alignment,
    verify_signature,
    zipalign,
)
from noir.infrastructure.apktool.adapter import (
    ApkToolAdapter,
    ApkToolError,
    overlay_preserved_entries,
)
from noir.infrastructure.filesystem.workspace import compute_file_hash
from noir.patches.engine import PatchEngine


class CertificationConfig(NoirConfig):
    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)


def _preserved(name: str) -> bool:
    upper = name.upper()
    signature = upper == "META-INF/MANIFEST.MF" or re.fullmatch(
        r"META-INF/[^/]+\.(?:SF|RSA|DSA|EC)", upper
    )
    return name != "AndroidManifest.xml" and not signature


def _opaque_hashes(apk: Path) -> dict[str, str]:
    with zipfile.ZipFile(apk) as archive:
        return {
            item.filename: hashlib.sha256(archive.read(item.filename)).hexdigest()
            for item in archive.infolist()
            if _preserved(item.filename)
        }


def _verify_opaque(apk: Path, expected: dict[str, str]) -> None:
    with zipfile.ZipFile(apk) as archive:
        names = set(archive.namelist())
        for name, digest in expected.items():
            if name not in names:
                raise RuntimeError(f"rebuilt APK omitted preserved entry {name}")
            actual = hashlib.sha256(archive.read(name)).hexdigest()
            if actual != digest:
                raise RuntimeError(f"rebuilt APK changed preserved entry {name}")


def _generate_keystore(directory: Path) -> tuple[Path, str, str]:
    keystore = directory / "certification.jks"
    alias = "noir-certification"
    password = secrets.token_urlsafe(24)
    environment = {**os.environ, "NOIR_CERT_PASS": password}
    result = subprocess.run(
        [
            "keytool",
            "-genkeypair",
            "-alias",
            alias,
            "-keyalg",
            "RSA",
            "-keysize",
            "2048",
            "-validity",
            "2",
            "-keystore",
            str(keystore),
            "-storepass:env",
            "NOIR_CERT_PASS",
            "-keypass:env",
            "NOIR_CERT_PASS",
            "-dname",
            "CN=NOIR Certification, O=NOIR, C=XX",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"keytool failed: {result.stderr.strip()}")
    return keystore, alias, password


def _operation_matrix(enabled: bool) -> list[tuple[str, OperationSpec]]:
    if not enabled:
        return [
            (
                "combined",
                OperationSpec(
                    app_name="NOIR Certified",
                    launch_url="https://example.com",
                    interaction_toast="NOIR certified",
                ),
            )
        ]
    features = ("rename", "redirect", "toast")
    matrix: list[tuple[str, OperationSpec]] = []
    for mask in range(1, 1 << len(features)):
        selected = [features[index] for index in range(3) if mask & (1 << index)]
        matrix.append(
            (
                "+".join(selected),
                OperationSpec(
                    app_name="NOIR Certified" if "rename" in selected else None,
                    launch_url="https://example.com" if "redirect" in selected else None,
                    interaction_toast="NOIR certified" if "toast" in selected else None,
                ),
            )
        )
    return matrix


def _build_with_retry(adapter: ApkToolAdapter, decoded: Path, output: Path) -> None:
    for attempt in (1, 2):
        output.unlink(missing_ok=True)
        try:
            adapter.build(decoded, output)
            return
        except ApkToolError as exc:
            if attempt == 2:
                details = exc.result.stderr or exc.result.stdout if exc.result else ""
                raise RuntimeError(f"{exc}\n{details[-8000:]}") from exc
            for generated in (decoded / "build", decoded / "dist"):
                if generated.is_dir() and not generated.is_symlink():
                    shutil.rmtree(generated)


def certify(
    apk: Path,
    *,
    matrix: bool,
    root: Path,
    config: NoirConfig,
    signing: tuple[Path, str, str],
) -> None:
    apk = apk.resolve()
    if not apk.is_file() or not zipfile.is_zipfile(apk):
        raise RuntimeError(f"not a valid APK archive: {apk}")
    case_root = root / re.sub(r"[^A-Za-z0-9_.-]", "_", apk.stem)
    case_root.mkdir()
    input_dir = case_root / "input"
    base = case_root / "base"
    input_dir.mkdir()
    original = input_dir / apk.name
    shutil.copy2(apk, original)
    expected = _opaque_hashes(original)
    adapter = ApkToolAdapter(config)

    adapter.decode(original, base, manifest_only=True)
    baseline = case_root / "baseline.apk"
    _build_with_retry(adapter, base, baseline)
    adapter.compile_manifest(original, base / "AndroidManifest.xml", baseline)
    overlay_preserved_entries(original, baseline)
    _verify_opaque(baseline, expected)
    for generated in (base / "build", base / "dist"):
        if generated.is_dir() and not generated.is_symlink():
            shutil.rmtree(generated)

    for label, spec in _operation_matrix(matrix):
        operation_root = case_root / label
        decoded = operation_root / "decoded"
        changes = operation_root / "changes"
        output = operation_root / "unsigned.apk"
        operation_root.mkdir()
        shutil.copytree(base, decoded)
        changes.mkdir()
        workspace = SimpleNamespace(
            input_dir=input_dir,
            decoded_dir=decoded,
            changes_dir=changes,
            config=config,
        )
        request = f"certification:{label}"
        plan = create_deterministic_plan("certification", 0, request, spec)
        patch = create_deterministic_patch(workspace, plan, spec, compute_file_hash(original))
        PatchEngine(workspace).apply_patch(patch)
        _build_with_retry(adapter, decoded, output)
        adapter.compile_manifest(original, decoded / "AndroidManifest.xml", output)
        overlay_preserved_entries(original, output)
        _verify_opaque(output, expected)
        with zipfile.ZipFile(output) as archive:
            if "AndroidManifest.xml" not in archive.namelist():
                raise RuntimeError(f"rebuilt APK omitted AndroidManifest.xml [{label}]")

        aligned = operation_root / "aligned.apk"
        signed = operation_root / "signed.apk"
        zipalign(config, output, aligned)
        if not verify_alignment(config, aligned)["aligned"]:
            raise RuntimeError(f"alignment verification failed for {apk.name} [{label}]")
        sign_apk(
            config,
            aligned,
            signed,
            keystore_path=str(signing[0]),
            key_alias=signing[1],
            keystore_password=signing[2],
        )
        if not verify_signature(config, signed)["verified"]:
            raise RuntimeError(f"signature verification failed for {apk.name} [{label}]")
        print(f"PASS {apk.name} [{label}]", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apks", nargs="+", type=Path)
    parser.add_argument("--matrix", action="store_true", help="run all seven combinations")
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="keep certification artifacts in this new directory",
    )
    args = parser.parse_args()
    sdk = Path(
        os.environ.get("ANDROID_SDK_ROOT")
        or os.environ.get("ANDROID_HOME")
        or "/opt/homebrew/share/android-commandlinetools"
    )
    installed_build_tools = (
        sorted(
            (path.name for path in (sdk / "build-tools").iterdir() if path.is_dir()),
            key=lambda value: tuple(
                int(part) if part.isdigit() else -1 for part in value.split(".")
            ),
        )
        if (sdk / "build-tools").is_dir()
        else []
    )
    build_tools_version = installed_build_tools[-1] if installed_build_tools else "36.0.0"
    if args.work_dir:
        root = args.work_dir.resolve()
        root.mkdir(parents=True, exist_ok=False)
        context = nullcontext(str(root))
    else:
        context = tempfile.TemporaryDirectory(prefix="noir-certification-")
    with context as temporary:
        root = Path(temporary)
        config = CertificationConfig(
            _env_file=None,
            data_dir=str(root / "data"),
            ai_provider="none",
            android_sdk_dir=str(sdk) if sdk.is_dir() else "",
            build_tools_version=build_tools_version,
        )
        config.ensure_directories()
        keystore, alias, password = _generate_keystore(root)
        installed_keystore = config.keys_dir / "certification.jks"
        shutil.move(keystore, installed_keystore)
        for apk in args.apks:
            certify(
                apk,
                matrix=args.matrix,
                root=root,
                config=config,
                signing=(installed_keystore, alias, password),
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
