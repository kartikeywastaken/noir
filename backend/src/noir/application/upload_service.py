"""Persistent, owner-scoped resumable APK uploads.

Each aligned range is written and fsynced before it is acknowledged. Ranges may
arrive out of order so clients can use several independent HTTP connections; a
lost response is recoverable from the persisted set of acknowledged offsets.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

from noir.application.access_service import AccessService
from noir.infrastructure.database.repositories import JobRepository


class UploadError(ValueError):
    """A client-visible resumable-upload failure."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class UploadSession:
    upload_id: str
    user_id: str
    idempotency_key: str
    filename: str
    size: int
    offset: int
    chunk_size: int
    created_at: float
    updated_at: float
    job_id: str | None = None
    sha256: str | None = None
    received_chunks: dict[str, dict[str, int | str]] = field(default_factory=dict)

    @property
    def received_bytes(self) -> int:
        return sum(int(chunk["length"]) for chunk in self.received_chunks.values())

    def refresh_contiguous_offset(self) -> None:
        cursor = 0
        for raw_offset, chunk in sorted(
            self.received_chunks.items(), key=lambda item: int(item[0])
        ):
            offset = int(raw_offset)
            if offset != cursor:
                break
            cursor += int(chunk["length"])
        self.offset = cursor

    def public(self) -> dict:
        return {
            "upload_id": self.upload_id,
            "filename": self.filename,
            "size": self.size,
            "offset": self.offset,
            "chunk_size": self.chunk_size,
            "complete": self.offset == self.size,
            "job_id": self.job_id,
            "received_bytes": self.received_bytes,
            "received_offsets": sorted(int(value) for value in self.received_chunks),
        }


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.RLock()


def _upload_lock(upload_id: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(upload_id, threading.RLock())


class ResumableUploadService:
    """Store resumable sessions outside project workspaces until finalized."""

    def __init__(self, config, queue):
        self.config = config
        self.queue = queue
        self.root = Path(config.data_dir) / "uploads" / "sessions"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if config.upload_chunk_size <= 0:
            raise ValueError("upload_chunk_size must be positive")
        if config.upload_chunk_size > config.max_upload_chunk_size:
            raise ValueError("upload_chunk_size exceeds max_upload_chunk_size")

    def _paths(self, upload_id: str) -> tuple[Path, Path]:
        if re.fullmatch(r"[a-f0-9]{32}", upload_id) is None:
            raise UploadError("Upload not found", 404)
        return self.root / f"{upload_id}.json", self.root / f"{upload_id}.apk"

    def _save(self, session: UploadSession) -> None:
        metadata, _ = self._paths(session.upload_id)
        temporary = metadata.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(asdict(session), separators=(",", ":")))
        temporary.chmod(0o600)
        os.replace(temporary, metadata)

    def _load(self, upload_id: str, user_id: str) -> UploadSession:
        metadata, part = self._paths(upload_id)
        try:
            raw = json.loads(metadata.read_text())
            session = UploadSession(**raw)
        except (FileNotFoundError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise UploadError("Upload not found", 404) from exc
        if session.upload_id != upload_id or session.user_id != user_id:
            raise UploadError("Upload not found", 404)
        if session.job_id is None:
            try:
                actual = part.stat().st_size
            except FileNotFoundError as exc:
                raise UploadError("Upload data is missing; start again", 410) from exc
            if actual > session.size:
                raise UploadError("Upload data exceeds declared size", 409)
            # Upgrade an in-progress session created by the earlier sequential
            # protocol. New range writes are recovered by safely overwriting a
            # chunk when its fsync completed before metadata replacement.
            if not session.received_chunks and session.offset > 0:
                legacy_offset = session.offset
                with part.open("rb") as handle:
                    for offset in range(0, legacy_offset, session.chunk_size):
                        length = min(session.chunk_size, legacy_offset - offset)
                        handle.seek(offset)
                        chunk = handle.read(length)
                        if len(chunk) != length:
                            raise UploadError("Upload data is incomplete", 409)
                        session.received_chunks[str(offset)] = {
                            "length": length,
                            "sha256": hashlib.sha256(chunk).hexdigest(),
                        }
                session.refresh_contiguous_offset()
                session.updated_at = time.time()
                self._save(session)
        return session

    def _cleanup_expired(self) -> None:
        cutoff = time.time() - self.config.upload_session_ttl
        for metadata in self.root.glob("*.json"):
            try:
                raw = json.loads(metadata.read_text())
                updated = float(raw["updated_at"])
                upload_id = str(raw["upload_id"])
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if updated >= cutoff:
                continue
            with _upload_lock(upload_id):
                metadata.unlink(missing_ok=True)
                (self.root / f"{upload_id}.apk").unlink(missing_ok=True)

    @staticmethod
    def _safe_filename(filename: str) -> str:
        name = Path(filename.replace("\\", "/")).name.strip()
        if not name or len(name) > 255:
            raise UploadError("Invalid APK filename")
        return name

    def begin(
        self, *, user_id: str, idempotency_key: str, filename: str, size: int
    ) -> UploadSession:
        if not idempotency_key or len(idempotency_key) > 256:
            raise UploadError("A valid Idempotency-Key is required")
        if size <= 0:
            raise UploadError("APK size must be positive")
        if size > self.config.max_upload_size:
            raise UploadError("APK exceeds upload limit", 413)
        filename = self._safe_filename(filename)
        self._cleanup_expired()
        with _LOCKS_GUARD:
            for metadata in self.root.glob("*.json"):
                try:
                    existing = UploadSession(**json.loads(metadata.read_text()))
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if existing.user_id != user_id or existing.idempotency_key != idempotency_key:
                    continue
                if existing.filename != filename or existing.size != size:
                    raise UploadError("Idempotency key has different upload metadata", 409)
                with _upload_lock(existing.upload_id):
                    return self._load(existing.upload_id, user_id)

            now = time.time()
            session = UploadSession(
                upload_id=uuid4().hex,
                user_id=user_id,
                idempotency_key=idempotency_key,
                filename=filename,
                size=size,
                offset=0,
                chunk_size=self.config.upload_chunk_size,
                created_at=now,
                updated_at=now,
            )
            _, part = self._paths(session.upload_id)
            part.touch(mode=0o600, exist_ok=False)
            self._save(session)
            return session

    def status(self, *, upload_id: str, user_id: str) -> UploadSession:
        with _upload_lock(upload_id):
            return self._load(upload_id, user_id)

    def append(
        self, *, upload_id: str, user_id: str, offset: int, chunk: bytes
    ) -> UploadSession:
        if not chunk:
            raise UploadError("Upload chunk is empty")
        if len(chunk) > self.config.max_upload_chunk_size:
            raise UploadError("Upload chunk exceeds server limit", 413)
        with _upload_lock(upload_id):
            session = self._load(upload_id, user_id)
            if session.job_id is not None:
                raise UploadError("Upload is already finalized", 409)
            if offset < 0 or offset >= session.size or offset % session.chunk_size != 0:
                raise UploadError("Upload offset is not chunk-aligned", 409)
            expected_length = min(session.chunk_size, session.size - offset)
            if len(chunk) != expected_length:
                raise UploadError(
                    f"Upload chunk length mismatch; expected {expected_length}", 409
                )
            digest = hashlib.sha256(chunk).hexdigest()
            existing = session.received_chunks.get(str(offset))
            if existing:
                if int(existing["length"]) != len(chunk) or existing["sha256"] != digest:
                    raise UploadError("Upload chunk differs from acknowledged content", 409)
                return session
            _, part = self._paths(upload_id)
            with part.open("r+b") as handle:
                handle.seek(offset)
                handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            session.received_chunks[str(offset)] = {
                "length": len(chunk),
                "sha256": digest,
            }
            session.refresh_contiguous_offset()
            session.updated_at = time.time()
            self._save(session)
            return session

    def complete(self, *, upload_id: str, user_id: str):
        with _upload_lock(upload_id):
            session = self._load(upload_id, user_id)
            if session.job_id:
                job = JobRepository().get(session.job_id)
                if job:
                    return job
            scoped_key = f"{user_id}:{session.idempotency_key}"
            existing = JobRepository().find_by_idempotency(scoped_key, user_id=user_id)
            if existing:
                session.job_id = existing.job_id
                session.updated_at = time.time()
                self._save(session)
                return existing
            if session.offset != session.size or session.received_bytes != session.size:
                raise UploadError(
                    f"Upload is incomplete; expected {session.size}, "
                    f"received {session.received_bytes}",
                    409,
                )
            _, part = self._paths(upload_id)
            hasher = hashlib.sha256()
            with part.open("rb") as handle:
                while block := handle.read(1024 * 1024):
                    hasher.update(block)
            digest = hasher.hexdigest()
            project_id = uuid4().hex[:16]
            AccessService().claim_project(user_id, project_id)
            try:
                job = self.queue.submit(
                    "import",
                    project_id,
                    {
                        "path": str(part),
                        "sha256": digest,
                        "size": session.size,
                        "move_input": True,
                        "original_filename": session.filename,
                    },
                    scoped_key,
                )
            except ValueError as exc:
                raise UploadError(str(exc), 409) from exc
            session.sha256 = digest
            session.job_id = job.job_id
            session.updated_at = time.time()
            self._save(session)
            return job
