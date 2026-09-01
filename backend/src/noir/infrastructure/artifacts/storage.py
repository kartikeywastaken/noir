"""Private S3 storage for durable APK artifacts.

Decoded workspaces and build intermediates intentionally remain on local EBS:
APKTool performs thousands of small filesystem operations that object storage
cannot serve efficiently. Only immutable originals and verified signed APKs are
mirrored here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class ArtifactStoreError(RuntimeError):
    """Raised when configured durable artifact storage is unavailable."""


class ArtifactStore:
    """Owner-scoped durable artifact store with a no-op local mode."""

    def __init__(self, config, *, client: Any | None = None):
        self.config = config
        self.enabled = config.artifact_store == "s3"
        self.bucket = config.s3_bucket.strip()
        self.prefix = config.s3_prefix.strip("/") or "noir"
        self._client = client
        if self.enabled and not self.bucket:
            raise ArtifactStoreError("S3 artifact storage requires NOIR_S3_BUCKET")

    @staticmethod
    def _identifier(value: str, label: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            raise ArtifactStoreError(f"Invalid {label} for artifact storage")
        return value

    def _s3(self):
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore[import-untyped]
            from botocore.config import Config  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ArtifactStoreError("boto3 is required for S3 artifact storage") from exc
        kwargs: dict[str, Any] = {
            "config": Config(
                signature_version="s3v4",
                retries={"max_attempts": 4, "mode": "standard"},
                s3={"addressing_style": "virtual", "use_global_endpoint": False},
            )
        }
        if self.config.s3_region:
            kwargs["region_name"] = self.config.s3_region
            # New buckets can temporarily redirect the legacy global endpoint.
            # A redirect changes the host and invalidates a SigV4 presigned URL,
            # so sign against the regional endpoint from the outset.
            kwargs["endpoint_url"] = (
                f"https://s3.{self.config.s3_region}.amazonaws.com"
            )
        self._client = boto3.client("s3", **kwargs)
        return self._client

    def _key(self, user_id: str, project_id: str, *parts: str) -> str:
        user = self._identifier(user_id, "user ID")
        project = self._identifier(project_id, "project ID")
        safe_parts = []
        for part in parts:
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", part):
                raise ArtifactStoreError("Invalid artifact key component")
            safe_parts.append(part)
        return "/".join(
            [self.prefix, "users", user, "projects", project, *safe_parts]
        )

    def original_key(self, user_id: str, project_id: str) -> str:
        return self._key(user_id, project_id, "original", "input.apk")

    def signed_key(self, user_id: str, project_id: str, build_id: str) -> str:
        build = self._identifier(build_id, "build ID")
        return self._key(user_id, project_id, "builds", build, "signed.apk")

    def _put_apk(
        self,
        path: Path,
        key: str,
        *,
        sha256: str,
        project_id: str,
        artifact_type: str,
    ) -> str | None:
        if not self.enabled:
            return None
        if not path.is_file():
            raise ArtifactStoreError(f"Artifact is missing: {path.name}")
        try:
            self._s3().upload_file(
                str(path),
                self.bucket,
                key,
                ExtraArgs={
                    "ContentType": "application/vnd.android.package-archive",
                    "ServerSideEncryption": "AES256",
                    "Metadata": {
                        "sha256": sha256,
                        "project-id": project_id,
                        "artifact-type": artifact_type,
                    },
                },
            )
        except Exception as exc:
            raise ArtifactStoreError(f"S3 upload failed for {artifact_type}: {exc}") from exc
        return key

    def store_original(
        self, user_id: str, project_id: str, path: Path, *, sha256: str
    ) -> str | None:
        return self._put_apk(
            path,
            self.original_key(user_id, project_id),
            sha256=sha256,
            project_id=project_id,
            artifact_type="original_apk",
        )

    def store_signed(
        self,
        user_id: str,
        project_id: str,
        build_id: str,
        path: Path,
        *,
        sha256: str,
    ) -> str | None:
        return self._put_apk(
            path,
            self.signed_key(user_id, project_id, build_id),
            sha256=sha256,
            project_id=project_id,
            artifact_type="signed_apk",
        )

    def exists(self, key: str, *, expected_sha256: str | None = None) -> bool:
        if not self.enabled:
            return False
        try:
            response = self._s3().head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            code = str(
                getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            )
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise ArtifactStoreError(f"S3 artifact lookup failed: {exc}") from exc
        if expected_sha256:
            actual = (response.get("Metadata") or {}).get("sha256")
            return actual == expected_sha256
        return True

    def signed_download_url(
        self,
        user_id: str,
        project_id: str,
        build_id: str,
        *,
        expected_sha256: str,
        filename: str,
    ) -> str | None:
        if not self.enabled:
            return None
        key = self.signed_key(user_id, project_id, build_id)
        if not self.exists(key, expected_sha256=expected_sha256):
            return None
        safe_filename = re.sub(r"[^A-Za-z0-9_.-]", "_", filename)[:128] or "noir.apk"
        try:
            url = self._s3().generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": key,
                    "ResponseContentType": "application/vnd.android.package-archive",
                    "ResponseContentDisposition": f'attachment; filename="{safe_filename}"',
                },
                ExpiresIn=self.config.s3_presign_expiry,
            )
            return str(url)
        except Exception as exc:
            raise ArtifactStoreError(f"S3 download authorization failed: {exc}") from exc
