"""Invite-only identities and server-enforced workspace ownership.

No public registration, shared client secret, or client-selected owner IDs.
The existing CLI remains the trusted local administration interface.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import update

from noir.domain.config import NoirConfig
from noir.domain.enums import SigningProfileType
from noir.domain.models import SigningProfile
from noir.infrastructure.database.engine import (
    BuildRow,
    InviteRow,
    ProjectAccessRow,
    ProjectRow,
    SigningAccessRow,
    TokenAccessRow,
    TokenRow,
    UserRow,
    get_session,
)


class AccessError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def normalize_invite(code: str) -> str:
    """Accept a raw code or copied CLI/JSON output, never alter its case."""
    code = code.strip()
    if code.startswith("{"):
        try:
            code = json.loads(code)["invite_code"]
        except (ValueError, KeyError, TypeError):
            raise AccessError("Paste the invitation code, not a user ID or bearer token") from None
    elif "invite_code:" in code:
        code = code.split("invite_code:", 1)[1].split("expires_at:", 1)[0]
    if not isinstance(code, str):
        raise AccessError("Invitation code must be text")
    code = "".join(code.strip(" \t\r\n`\"'").split())
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", code):
        raise AccessError(
            "Copy the complete 43-character invite_code, not the user_id or invite_id"
        )
    return code


class AccessService:
    def user(self, user_id: str) -> dict:
        with get_session() as session:
            user = session.get(UserRow, user_id)
            if user is None or user.disabled:
                raise AccessError("Workspace access is unavailable")
            return {"user_id": user.user_id, "name": user.name}

    def invite(self, name: str, *, user_id: str | None = None, hours: int = 168) -> dict:
        name = name.strip()
        if not 1 <= len(name) <= 80 or not 1 <= hours <= 720:
            raise AccessError("Provide a name of 1–80 characters and expiry of 1–720 hours")
        code = secrets.token_urlsafe(32)
        expires = _now() + timedelta(hours=hours)
        with get_session() as session:
            if user_id is None:
                user = UserRow(user_id=uuid4().hex[:16], name=name, disabled=False)
                session.add(user)
            else:
                user = session.get(UserRow, user_id)
                if user is None or user.disabled:
                    raise AccessError("Workspace user not found or disabled")
            invite = InviteRow(
                invite_id=uuid4().hex[:16],
                user_id=user.user_id,
                code_hash=hashlib.sha256(code.encode()).hexdigest(),
                expires_at=expires,
                revoked=False,
            )
            session.add(invite)
            session.commit()
            return {
                "user_id": user.user_id,
                "name": user.name,
                "invite_id": invite.invite_id,
                "invite_code": code,
                "expires_at": expires.isoformat() + "Z",
            }

    def redeem(self, code: str) -> dict:
        digest = hashlib.sha256(normalize_invite(code).encode()).hexdigest()
        raw_token = secrets.token_urlsafe(48)
        token_id = uuid4().hex[:16]
        now = _now()
        with get_session() as session:
            invite = session.query(InviteRow).filter(InviteRow.code_hash == digest).first()
            user = session.get(UserRow, invite.user_id) if invite else None
            if invite is None:
                raise AccessError("Code not recognized. Check that you copied the full invite_code")
            if user is None or user.disabled or invite.revoked:
                raise AccessError("This invitation was revoked. Ask the owner for access")
            if invite.redeemed_at is not None:
                raise AccessError(
                    "This code was already used. Ask for a new code for the same workspace"
                )
            if invite.expires_at <= now:
                raise AccessError("This invitation expired. Ask the owner for a new code")
            # Conditional update consumes the invitation exactly once, including
            # simultaneous requests. Token creation commits in the same transaction.
            result = session.execute(
                update(InviteRow)
                .where(
                    InviteRow.invite_id == invite.invite_id,
                    InviteRow.redeemed_at.is_(None),
                    InviteRow.revoked.is_(False),
                    InviteRow.expires_at > now,
                )
                .values(redeemed_at=now)
            )
            if result.rowcount != 1:
                raise AccessError("Invite is invalid, expired, revoked, or already used")
            session.add(
                TokenRow(
                    token_id=token_id,
                    name=user.name,
                    token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                )
            )
            session.add(TokenAccessRow(token_id=token_id, user_id=user.user_id, revoked=False))
            session.commit()
            return {"token": raw_token, "user": {"user_id": user.user_id, "name": user.name}}

    def list_users(self) -> list[dict]:
        with get_session() as session:
            return [
                {"user_id": user.user_id, "name": user.name, "disabled": user.disabled}
                for user in session.query(UserRow).order_by(UserRow.created_at).all()
            ]

    def revoke_user(self, user_id: str) -> None:
        if user_id == "local":
            raise AccessError("Cannot disable the local owner workspace")
        with get_session() as session:
            user = session.get(UserRow, user_id)
            if user is None:
                raise AccessError("Workspace user not found")
            user.disabled = True
            session.query(TokenAccessRow).filter_by(user_id=user_id).update({"revoked": True})
            session.query(InviteRow).filter_by(user_id=user_id).update({"revoked": True})
            session.commit()

    def logout(self, token_id: str) -> None:
        with get_session() as session:
            session.query(TokenAccessRow).filter_by(token_id=token_id).update({"revoked": True})
            session.commit()

    def claim_project(self, user_id: str, project_id: str) -> None:
        with get_session() as session:
            # Never reassign a project, even if no decoded files exist yet.
            if session.get(ProjectAccessRow, project_id) is not None:
                raise AccessError("Workspace already reserved")
            session.add(ProjectAccessRow(project_id=project_id, user_id=user_id))
            session.commit()

    def owns_project(self, user_id: str, project_id: str) -> bool:
        with get_session() as session:
            access = session.get(ProjectAccessRow, project_id)
            return bool(access and access.user_id == user_id)

    def project_owner(self, project_id: str) -> str:
        """Return the server-side owner used to namespace private artifacts."""
        with get_session() as session:
            access = session.get(ProjectAccessRow, project_id)
            if access is None:
                raise AccessError("Workspace owner not found")
            return access.user_id

    def owns_signer(self, user_id: str, profile_name: str) -> bool:
        with get_session() as session:
            access = session.get(SigningAccessRow, profile_name)
            return bool(access and access.user_id == user_id)

    def history(self, user_id: str, *, offset: int, limit: int) -> dict:
        from noir.infrastructure.database.repositories import BuildRepository

        with get_session() as session:
            query = (
                session.query(BuildRow, ProjectRow)
                .join(ProjectRow, BuildRow.project_id == ProjectRow.id)
                .join(ProjectAccessRow, ProjectRow.id == ProjectAccessRow.project_id)
                .filter(ProjectAccessRow.user_id == user_id)
            )
            total = query.count()
            rows = (
                query.order_by(BuildRow.created_at.desc(), BuildRow.build_id.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            items = []
            for build, project in rows:
                data = BuildRepository()._to_model(build).model_dump(mode="json")
                data["created_at"] = build.created_at.replace(tzinfo=UTC).isoformat()
                # History needs hashes and status, not internal server paths/logs.
                for key in (
                    "unsigned_apk_path",
                    "aligned_apk_path",
                    "signed_apk_path",
                    "tool_logs",
                ):
                    data.pop(key, None)
                data.update(
                    {
                        "original_filename": project.original_filename,
                        "package_name": project.package_name,
                        "current_revision": project.workspace_revision,
                        "project_dirty": project.dirty,
                    }
                )
                items.append(data)
            return {"builds": items, "total": total, "offset": offset, "limit": limit}

    def ensure_personal_signer(self, config: NoirConfig, user_id: str) -> SigningProfile:
        """Explicit provisioning endpoint; each workspace receives a distinct key."""
        from noir.application.signing_service import SigningService
        from noir.infrastructure.database.repositories import SigningProfileRepository
        from noir.infrastructure.processes.runner import run_tool

        self.user(user_id)
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", user_id):
            raise AccessError("Invalid workspace identifier")
        password = os.environ.get("NOIR_KEYSTORE_PASSWORD", "")
        if not password:
            raise AccessError(
                "Personal signing is not configured on this server. Contact the owner."
            )
        directory = config.keys_dir / "users" / user_id
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        name = f"personal-{user_id}"
        keystore = directory / "signing.jks"
        with (directory / ".provision.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            existing = SigningProfileRepository().get(name)
            if existing:
                if not self.owns_signer(user_id, name):
                    raise AccessError("Signing profile is unavailable")
                return existing
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
                        "NOIR_PERSONAL_SIGN_PASS",
                        "-keypass:env",
                        "NOIR_PERSONAL_SIGN_PASS",
                        "-dname",
                        "CN=NOIR Personal Test, O=NOIR, C=XX",
                    ],
                    timeout=45,
                    tool_name="keytool",
                    env={"NOIR_PERSONAL_SIGN_PASS": password},
                )
                if result.exit_code:
                    raise AccessError("Could not create personal signing key")
                keystore.chmod(0o600)
            fingerprint = SigningService(config)._get_cert_fingerprint(
                str(keystore), name, password
            )
            profile = SigningProfile(
                name=name,
                profile_type=SigningProfileType.USER_SUPPLIED,
                keystore_path=str(keystore),
                key_alias=name,
                certificate_fingerprint_sha256=fingerprint,
            )
            return SigningProfileRepository().create(profile, user_id=user_id)
