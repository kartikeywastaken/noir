"""Signing profile manager and signing service."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import tempfile
from pathlib import Path

from noir.domain.config import NoirConfig, get_config
from noir.domain.enums import (
    ApprovalScope,
    EventSeverity,
    ProjectStatus,
    SigningProfileType,
    WorkflowStage,
)
from noir.domain.models import (
    ApprovalRecord,
    AuditEvent,
    BuildResult,
    SigningProfile,
)
from noir.infrastructure.android_tools.tools import (
    ApksignerError,
    ZipalignError,
    sign_apk,
    verify_alignment,
    verify_signature,
    zipalign,
)
from noir.infrastructure.database.repositories import (
    ApprovalRepository,
    BuildRepository,
    EventRepository,
    ProjectRepository,
    SigningProfileRepository,
)
from noir.infrastructure.filesystem.workspace import compute_file_hash
from noir.infrastructure.processes.runner import run_tool
from noir.security.locking import locked_project, require_clean_workspace


class SigningServiceError(Exception):
    pass


class SigningService:
    """Manages signing profiles and APK signing operations."""

    def __init__(self, config: NoirConfig | None = None):
        self.config = config or get_config()
        self.profile_repo = SigningProfileRepository()
        self.project_repo = ProjectRepository()
        self.build_repo = BuildRepository()
        self.event_repo = EventRepository()
        self.approval_repo = ApprovalRepository()

    def create_debug_profile(self, name: str) -> SigningProfile:
        """Create a persistent local debug/test signing profile."""
        self._validate_name(name)
        keys_dir = self.config.keys_dir
        keys_dir.mkdir(parents=True, exist_ok=True)

        keystore_path = keys_dir / f"{name}.jks"
        if keystore_path.exists():
            raise SigningServiceError(
                f"Keystore already exists at {keystore_path}. "
                "Will not overwrite existing key material."
            )

        password = secrets.token_urlsafe(24)

        # Generate keystore with keytool
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
                str(keystore_path),
                "-storepass:env",
                "NOIR_SIGN_STORE_PASS",
                "-keypass:env",
                "NOIR_SIGN_STORE_PASS",
                "-dname",
                f"CN=NOIR Debug ({name}), OU=Debug, O=NOIR, L=Local, ST=Dev, C=XX",
            ],
            timeout=30,
            tool_name="keytool",
            env={"NOIR_SIGN_STORE_PASS": password},
        )

        if result.exit_code != 0:
            raise SigningServiceError(f"keytool failed: {result.stderr}")

        os.chmod(keystore_path, 0o600)
        try:
            self._store_password(name, password)
        except Exception:
            keystore_path.unlink(missing_ok=True)
            raise

        # Get certificate fingerprint
        fingerprint = self._get_cert_fingerprint(str(keystore_path), name, password)

        profile = SigningProfile(
            name=name,
            profile_type=SigningProfileType.PERSISTENT_LOCAL,
            keystore_path=str(keystore_path),
            key_alias=name,
            certificate_fingerprint_sha256=fingerprint,
        )
        self.profile_repo.create(profile)

        return profile

    def add_user_profile(
        self, name: str, keystore_path: str, alias: str, *, password: str | None = None
    ) -> SigningProfile:
        """Register a user-supplied keystore as a signing profile."""
        self._validate_name(name)
        ks = Path(keystore_path)
        if not ks.exists():
            raise SigningServiceError(f"Keystore not found: {keystore_path}")

        profile = SigningProfile(
            name=name,
            profile_type=SigningProfileType.USER_SUPPLIED,
            keystore_path=str(ks.resolve()),
            key_alias=alias,
        )
        if password:
            profile.certificate_fingerprint_sha256 = self._get_cert_fingerprint(
                str(ks.resolve()), alias, password
            )
            self._store_password(name, password)
        self.profile_repo.create(profile)
        return profile

    @staticmethod
    def _validate_name(name):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            raise SigningServiceError("Profile name must contain only letters, digits, _ or -")

    def _store_password(self, name, password):
        import keyring

        try:
            keyring.set_password(f"noir:{Path(self.config.data_dir).resolve()}", name, password)
        except Exception as exc:
            raise SigningServiceError(
                "OS credential storage unavailable; no plaintext fallback"
            ) from exc

    def list_profiles(self) -> list[SigningProfile]:
        return self.profile_repo.list_all()

    def get_profile(self, name: str) -> SigningProfile | None:
        return self.profile_repo.get(name)

    @locked_project
    def sign(
        self,
        project_id: str,
        build_id: str,
        profile_name: str,
        *,
        password: str | None = None,
        key_password: str | None = None,
        confirmed: bool = False,
    ) -> BuildResult:
        """Align and sign a built APK.

        Order: zipalign → verify alignment → apksigner sign → apksigner verify

        Args:
            project_id: Project ID.
            build_id: Build ID with unsigned APK.
            profile_name: Signing profile name.
            password: Keystore password (prompted or from secure store).
        """
        if not confirmed:
            raise SigningServiceError("Signing requires explicit confirmation")
        self._validate_name(profile_name)
        project = self.project_repo.get(project_id)
        if not project:
            raise SigningServiceError(f"Project not found: {project_id}")

        build = self.build_repo.get(build_id)
        if not build or build.project_id != project_id:
            raise SigningServiceError(f"Build not found: {build_id}")
        if not build.success or not build.unsigned_apk_path:
            raise SigningServiceError("Build was not successful, cannot sign")
        if build.workspace_revision != project.workspace_revision:
            raise SigningServiceError("Build belongs to a stale workspace revision")
        require_clean_workspace(self.config, project_id)
        if compute_file_hash(Path(build.unsigned_apk_path)) != build.unsigned_apk_hash:
            raise SigningServiceError("Unsigned artifact hash changed since rebuilding")

        profile = self.profile_repo.get(profile_name)
        if not profile:
            raise SigningServiceError(f"Signing profile not found: {profile_name}")
        if not profile.keystore_path:
            raise SigningServiceError("Profile has no keystore path")

        # Get password
        actual_password = password or os.environ.get("NOIR_KEYSTORE_PASSWORD")
        if not actual_password:
            import keyring

            try:
                actual_password = keyring.get_password(
                    f"noir:{Path(self.config.data_dir).resolve()}", profile_name
                )
            except Exception as exc:
                raise SigningServiceError("Cannot access OS credential storage") from exc

        if not actual_password:
            raise SigningServiceError(
                "Keystore password required. Use the secure CLI prompt or NOIR_KEYSTORE_PASSWORD."
            )

        unsigned_apk = Path(build.unsigned_apk_path)
        build_dir = unsigned_apk.parent
        fingerprint = self._get_cert_fingerprint(
            profile.keystore_path, profile.key_alias or profile.name, actual_password
        )
        import hashlib

        binding = hashlib.sha256(f"{build.unsigned_apk_hash}:{fingerprint}".encode()).hexdigest()
        self.approval_repo.create(
            ApprovalRecord(
                project_id=project_id,
                scope=ApprovalScope.SIGNING,
                workspace_revision=project.workspace_revision,
                target_hash=binding,
                target_id=build_id,
            )
        )

        try:
            # Step 1: zipalign
            aligned_apk = build_dir / "aligned.apk"
            zipalign(self.config, unsigned_apk, aligned_apk)

            # Step 2: verify alignment
            align_result = verify_alignment(self.config, aligned_apk)
            if not align_result["aligned"]:
                raise SigningServiceError("Alignment verification failed")

            build.aligned_apk_path = str(aligned_apk)
            build.aligned_apk_hash = compute_file_hash(aligned_apk)

            # Step 3: sign
            signed_apk = build_dir / "signed.apk"
            sign_apk(
                self.config,
                aligned_apk,
                signed_apk,
                keystore_path=profile.keystore_path,
                key_alias=profile.key_alias or profile.name,
                keystore_password=actual_password,
                key_password=key_password or os.environ.get("NOIR_KEY_PASSWORD"),
            )

            # Step 4: verify signature
            sig_result = verify_signature(self.config, signed_apk)
            if not sig_result["verified"]:
                raise SigningServiceError(
                    f"Signature verification failed: {sig_result.get('output', '')}"
                )

            build.signed_apk_path = str(signed_apk)
            build.signed_apk_hash = compute_file_hash(signed_apk)
            self.build_repo.update(build)

            # Update project
            project.status = ProjectStatus.SIGNED
            self.project_repo.update(project)

            self.event_repo.create(
                AuditEvent(
                    project_id=project_id,
                    stage=WorkflowStage.SIGNING,
                    severity=EventSeverity.INFO,
                    message=f"APK signed with profile '{profile_name}'",
                    metadata={
                        "build_id": build_id,
                        "profile": profile_name,
                        "signed_hash": build.signed_apk_hash,
                        "cert_info": sig_result.get("cert_info", {}),
                    },
                )
            )

            return build

        except (ZipalignError, ApksignerError) as e:
            self.event_repo.create(
                AuditEvent(
                    project_id=project_id,
                    stage=WorkflowStage.SIGNING,
                    severity=EventSeverity.ERROR,
                    message=f"Signing failed: {e}",
                )
            )
            raise SigningServiceError(str(e)) from e

    def create_ephemeral_profile(self) -> tuple[SigningProfile, str, Path]:
        """Create a temporary ephemeral signing profile.

        Returns (profile, password, keystore_path).
        The caller must clean up the keystore after use.
        """
        tmp_dir = Path(tempfile.mkdtemp(prefix="noir_ephemeral_"))
        keystore_path = tmp_dir / "ephemeral.jks"
        password = secrets.token_urlsafe(24)
        alias = "ephemeral"

        result = run_tool(
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
                "1",
                "-keystore",
                str(keystore_path),
                "-storepass:env",
                "NOIR_SIGN_STORE_PASS",
                "-keypass:env",
                "NOIR_SIGN_STORE_PASS",
                "-dname",
                "CN=NOIR Ephemeral, OU=Test, O=NOIR, C=XX",
            ],
            timeout=30,
            tool_name="keytool",
            env={"NOIR_SIGN_STORE_PASS": password},
        )

        if result.exit_code != 0:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise SigningServiceError(f"Failed to create ephemeral key: {result.stderr}")

        os.chmod(keystore_path, 0o600)
        profile = SigningProfile(
            name=f"ephemeral_{secrets.token_hex(4)}",
            profile_type=SigningProfileType.EPHEMERAL_DEBUG,
            keystore_path=str(keystore_path),
            key_alias=alias,
        )

        return profile, password, keystore_path

    def _get_cert_fingerprint(self, keystore_path: str, alias: str, password: str) -> str:
        """Get the SHA-256 certificate fingerprint."""
        result = run_tool(
            [
                "keytool",
                "-list",
                "-v",
                "-keystore",
                keystore_path,
                "-alias",
                alias,
                "-storepass:env",
                "NOIR_SIGN_STORE_PASS",
            ],
            timeout=15,
            tool_name="keytool",
            env={"NOIR_SIGN_STORE_PASS": password},
        )
        if result.exit_code != 0:
            raise SigningServiceError("Cannot open keystore or read certificate")
        for line in result.stdout.splitlines():
            if "SHA256" in line or "SHA-256" in line:
                parts = line.split(":", 1)
                if len(parts) > 1:
                    return parts[1].strip()
        raise SigningServiceError("Certificate SHA-256 fingerprint could not be read")
