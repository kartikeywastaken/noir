"""Private S3 storage for durable APK artifacts.

Decoded workspaces and build intermediates intentionally remain on local EBS:
APKTool performs thousands of small filesystem operations that object storage
cannot serve efficiently. Only immutable originals and verified signed APKs are
mirrored here.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

try:
    from boto3.s3.transfer import TransferConfig as S3TransferConfig

    _TRANSFER_CFG = S3TransferConfig(
        multipart_threshold=16 * 1024 * 1024,  # 16 MiB before switching to multipart
        multipart_chunksize=16 * 1024 * 1024,
        max_concurrency=4,  # parallel part uploads
        use_threads=True,
    )
except ImportError:  # boto3 not available in local/test environments
    _TRANSFER_CFG = None  # type: ignore[assignment]


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
                s3=cast(Any, {"addressing_style": "virtual", "use_global_endpoint": False}),
            )
        }
        if self.config.s3_region:
            kwargs["region_name"] = self.config.s3_region
            # New buckets can temporarily redirect the legacy global endpoint.
            # A redirect changes the host and invalidates a SigV4 presigned URL,
            # so sign against the regional endpoint from the outset.
            kwargs["endpoint_url"] = f"https://s3.{self.config.s3_region}.amazonaws.com"
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
        return "/".join([self.prefix, "users", user, "projects", project, *safe_parts])

    def original_key(self, user_id: str, project_id: str) -> str:
        return self._key(user_id, project_id, "original", "input.apk")

    def signed_key(self, user_id: str, project_id: str, build_id: str) -> str:
        build = self._identifier(build_id, "build ID")
        return self._key(user_id, project_id, "builds", build, "signed.apk")

    def begin_multipart_original(
        self,
        user_id: str,
        project_id: str,
        *,
        sha256: str,
        size: int,
    ) -> tuple[str, str]:
        """Create a private, checksum-aware multipart upload for an original APK."""
        if not self.enabled:
            raise ArtifactStoreError("Direct upload requires S3 artifact storage")
        if not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise ArtifactStoreError("Invalid APK SHA-256")
        if size <= 0:
            raise ArtifactStoreError("Invalid APK size")
        key = self.original_key(user_id, project_id)
        try:
            response = self._s3().create_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                ContentType="application/vnd.android.package-archive",
                ServerSideEncryption="AES256",
                ChecksumAlgorithm="SHA256",
                Metadata={
                    "sha256": sha256,
                    "size": str(size),
                    "project-id": project_id,
                    "artifact-type": "original_apk",
                },
            )
            upload_id = str(response["UploadId"])
        except Exception as exc:
            raise ArtifactStoreError(f"S3 multipart upload creation failed: {exc}") from exc
        if not upload_id:
            raise ArtifactStoreError("S3 did not return a multipart upload ID")
        return key, upload_id

    def presign_multipart_part(
        self,
        *,
        key: str,
        upload_id: str,
        part_number: int,
        checksum_sha256: str,
    ) -> str:
        """Authorize one exact S3 multipart part and bind its SHA-256 header."""
        if not self.enabled:
            raise ArtifactStoreError("Direct upload requires S3 artifact storage")
        try:
            url = self._s3().generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": self.bucket,
                    "Key": key,
                    "UploadId": upload_id,
                    "PartNumber": part_number,
                    "ChecksumSHA256": checksum_sha256,
                },
                ExpiresIn=self.config.s3_presign_expiry,
                HttpMethod="PUT",
            )
            return str(url)
        except Exception as exc:
            raise ArtifactStoreError(f"S3 part authorization failed: {exc}") from exc

    def complete_multipart_original(
        self,
        *,
        key: str,
        upload_id: str,
        parts: list[dict[str, Any]],
    ) -> None:
        if not self.enabled:
            raise ArtifactStoreError("Direct upload requires S3 artifact storage")
        try:
            self._s3().complete_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except Exception as exc:
            raise ArtifactStoreError(f"S3 multipart completion failed: {exc}") from exc

    def abort_multipart_upload(self, *, key: str, upload_id: str) -> None:
        if not self.enabled:
            return
        try:
            self._s3().abort_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
            )
        except Exception as exc:
            raise ArtifactStoreError(f"S3 multipart abort failed: {exc}") from exc

    def verify_original_object(
        self,
        *,
        key: str,
        expected_size: int,
        expected_sha256: str,
    ) -> None:
        """Verify server-controlled metadata and exact object length before import."""
        if not self.enabled:
            raise ArtifactStoreError("Direct upload requires S3 artifact storage")
        try:
            response = self._s3().head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise ArtifactStoreError(f"S3 artifact lookup failed: {exc}") from exc
        metadata = response.get("Metadata") or {}
        if int(response.get("ContentLength", -1)) != expected_size:
            raise ArtifactStoreError("S3 APK size does not match the declared upload size")
        if metadata.get("sha256") != expected_sha256:
            raise ArtifactStoreError("S3 APK checksum metadata does not match the upload session")
        if metadata.get("size") not in {None, str(expected_size)}:
            raise ArtifactStoreError("S3 APK size metadata does not match the upload session")

    def download_verified(
        self,
        *,
        key: str,
        destination: Path,
        expected_size: int,
        expected_sha256: str,
    ) -> tuple[str, int]:
        """Stream one private S3 object to disk while verifying exact bytes.

        The response is never accumulated in memory and the destination becomes
        visible only after length and SHA-256 verification both succeed.
        """
        if not self.enabled:
            raise ArtifactStoreError("S3 download requires S3 artifact storage")
        destination = destination.resolve()
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if destination.exists():
            raise ArtifactStoreError("S3 download destination already exists")
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        body = None
        try:
            response = self._s3().get_object(Bucket=self.bucket, Key=key)
            if int(response.get("ContentLength", -1)) != expected_size:
                raise ArtifactStoreError("S3 APK size changed before import")
            metadata = response.get("Metadata") or {}
            if metadata.get("sha256") != expected_sha256:
                raise ArtifactStoreError("S3 APK checksum metadata changed before import")
            body = response["Body"]
            digest = hashlib.sha256()
            size = 0
            with temporary.open("xb") as target:
                temporary.chmod(0o600)
                while chunk := body.read(1024 * 1024):
                    size += len(chunk)
                    if size > expected_size:
                        raise ArtifactStoreError("S3 APK exceeds its declared size")
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            actual_sha256 = digest.hexdigest()
            if size != expected_size:
                raise ArtifactStoreError("S3 APK is incomplete")
            if actual_sha256 != expected_sha256:
                raise ArtifactStoreError("S3 APK checksum verification failed")
            os.replace(temporary, destination)
            return actual_sha256, size
        except ArtifactStoreError:
            raise
        except Exception as exc:
            raise ArtifactStoreError(f"S3 APK download failed: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
            close = getattr(body, "close", None)
            if callable(close):
                close()

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
                Config=_TRANSFER_CFG,
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
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
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
