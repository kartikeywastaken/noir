"""Persistent, owner-scoped resumable APK uploads.

Each aligned range is written and fsynced before it is acknowledged. Ranges may
arrive out of order so clients can use several independent HTTP connections; a
lost response is recoverable from the persisted set of acknowledged offsets.

Performance optimizations (vs. the original sequential implementation):
  - Group-commit fsync: multiple chunk writes share a single fsync, with the
    HTTP acknowledgment held until that fsync completes.  "Acknowledged means
    durable" is preserved.
  - Append-only journal: per-chunk persistence is O(1), not O(session size).
    The full snapshot is only written on session creation, completion, and
    periodic compaction.
  - Split lock: the actual chunk data write runs without holding the upload
    lock (disjoint byte ranges are safe to write concurrently); only the brief
    bookkeeping section (received_chunks update, journal append) is serialized.
  - Deterministic idempotency lookup: O(1) file check instead of glob scan.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

from noir.application.access_service import AccessService
from noir.infrastructure.artifacts import ArtifactStore, ArtifactStoreError
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
            "upload_mode": "proxy",
            "upload_id": self.upload_id,
            "project_id": self.upload_id,  # placeholder — replaced by real project_id after import
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


# ── Group-commit fsync batcher ───────────────────────────────────────


@dataclass
class _PendingCommit:
    """One chunk writer waiting for the group-commit fsync to complete."""

    event: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None


class _CommitBatcher:
    """Batches fsync calls for a single upload's data file and journal.

    Writers add pending commits and block until the batch fsync completes.
    A background daemon thread wakes on each new pending commit and flushes
    when either the batch is full or the time window expires.

    Correctness guarantee: a writer's ``event`` is set (unblocking the HTTP
    response) only *after* ``os.fsync()`` succeeds for both the data file and
    the journal.  If the fsync fails, the error is propagated to every waiting
    writer so the HTTP response is an error, not a false acknowledgment.
    """

    def __init__(self, interval_ms: int, batch_max: int):
        self._interval = interval_ms / 1000.0  # seconds
        self._batch_max = batch_max
        self._lock = threading.Lock()
        self._pending: list[tuple[_PendingCommit, Path, Path, str]] = []
        self._wake = threading.Event()
        self._closed = False
        self._thread: threading.Thread | None = None

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        t = threading.Thread(target=self._run, daemon=True, name="commit-batcher")
        t.start()
        self._thread = t

    def submit(
        self, pending: _PendingCommit, data_path: Path, journal_path: Path, journal_line: str
    ) -> None:
        """Enqueue a commit and wake the batcher thread."""
        with self._lock:
            self._pending.append((pending, data_path, journal_path, journal_line))
            batch_full = len(self._pending) >= self._batch_max
            self._ensure_thread()
        if batch_full:
            self._wake.set()
        else:
            self._wake.set()

    def _run(self) -> None:
        while not self._closed:
            self._wake.wait(timeout=self._interval)
            self._wake.clear()
            with self._lock:
                batch = list(self._pending)
                self._pending.clear()
            if not batch:
                continue
            self._flush(batch)

    def _flush(self, batch: list[tuple[_PendingCommit, Path, Path, str]]) -> None:
        """Fsync data files and journals, then release all waiters."""
        # Group by data file to fsync each file at most once
        by_data: dict[str, list[tuple[_PendingCommit, Path, Path, str]]] = {}
        for item in batch:
            key = str(item[1])
            by_data.setdefault(key, []).append(item)

        for _data_key, group in by_data.items():
            data_path = group[0][1]
            error: BaseException | None = None
            try:
                # Fsync the data file once for all chunks in this group
                fd = os.open(str(data_path), os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)

                # Append all journal entries and fsync the journal once
                journal_path = group[0][2]
                lines = [item[3] for item in group]
                with journal_path.open("a", encoding="utf-8") as jf:
                    for line in lines:
                        jf.write(line + "\n")
                    jf.flush()
                    os.fsync(jf.fileno())
            except BaseException as exc:
                error = exc

            # Release all waiters for this data file
            for pending, _, _, _ in group:
                pending.error = error
                pending.event.set()

    def close(self) -> None:
        self._closed = True
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)


# ── Journal helpers ──────────────────────────────────────────────────

_JOURNAL_COMPACT_THRESHOLD = 256


def _replay_journal(journal_path: Path, session: UploadSession) -> bool:
    """Replay journal entries into session.received_chunks. Returns True if any applied."""
    if not journal_path.exists():
        return False
    applied = False
    for line in journal_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            offset_key = str(record["offset"])
            if offset_key not in session.received_chunks:
                session.received_chunks[offset_key] = {
                    "length": record["length"],
                    "sha256": record["sha256"],
                }
                applied = True
        except (json.JSONDecodeError, KeyError):
            continue  # skip malformed entries
    if applied:
        session.refresh_contiguous_offset()
    return applied


# ── Main service ─────────────────────────────────────────────────────


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
        self._batcher = _CommitBatcher(
            interval_ms=config.upload_fsync_interval_ms,
            batch_max=config.upload_fsync_batch_max,
        )
        # In-flight offset tracking for Phase 4 concurrent writes
        self._in_flight: dict[str, set[int]] = {}
        self._in_flight_lock = threading.Lock()

    def _paths(self, upload_id: str) -> tuple[Path, Path]:
        if re.fullmatch(r"[a-f0-9]{32}", upload_id) is None:
            raise UploadError("Upload not found", 404)
        return self.root / f"{upload_id}.json", self.root / f"{upload_id}.apk"

    def _journal_path(self, upload_id: str) -> Path:
        return self.root / f"{upload_id}.journal"

    def _idempotency_path(self, user_id: str, idempotency_key: str) -> Path:
        """Deterministic filename for O(1) idempotency lookup."""
        lookup = hashlib.sha256(f"{user_id}:{idempotency_key}".encode()).hexdigest()[:32]
        return self.root / f"idem-{lookup}.json"

    def _save(self, session: UploadSession) -> None:
        """Write a full session snapshot and remove the journal (compaction)."""
        metadata, _ = self._paths(session.upload_id)
        temporary = metadata.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(asdict(session), separators=(",", ":")))
        temporary.chmod(0o600)
        os.replace(temporary, metadata)
        # Remove journal on full snapshot (compaction)
        journal = self._journal_path(session.upload_id)
        journal.unlink(missing_ok=True)

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

            # Replay journal entries (Phase 3) — works alongside legacy upgrade
            journal = self._journal_path(upload_id)
            journal_applied = _replay_journal(journal, session)

            # Compact journal if it's grown too large
            if journal_applied and journal.exists():
                line_count = sum(1 for _ in journal.read_text().splitlines() if _.strip())
                if line_count >= _JOURNAL_COMPACT_THRESHOLD:
                    session.updated_at = time.time()
                    self._save(session)  # _save removes the journal
        return session

    def _cleanup_expired(self) -> None:
        cutoff = time.time() - self.config.upload_session_ttl
        for metadata in self.root.glob("*.json"):
            # Skip idempotency index files
            if metadata.name.startswith("idem-"):
                continue
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
                self._journal_path(upload_id).unlink(missing_ok=True)
                # Clean up idempotency index if present
                try:
                    raw_data = raw
                    user_id = str(raw_data.get("user_id", ""))
                    idem_key = str(raw_data.get("idempotency_key", ""))
                    if user_id and idem_key:
                        self._idempotency_path(user_id, idem_key).unlink(missing_ok=True)
                except (KeyError, TypeError):
                    pass

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

        # Phase 5a: O(1) deterministic idempotency lookup
        idem_path = self._idempotency_path(user_id, idempotency_key)
        if idem_path.exists():
            try:
                idem_data = json.loads(idem_path.read_text())
                existing_id = idem_data["upload_id"]
                with _upload_lock(existing_id):
                    existing = self._load(existing_id, user_id)
                    if existing.filename != filename or existing.size != size:
                        raise UploadError("Idempotency key has different upload metadata", 409)
                    return existing
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, UploadError):
                # Index is stale or corrupt — fall through to legacy scan
                pass

        with _LOCKS_GUARD:
            # Legacy fallback: scan all sessions for backward compatibility
            for metadata in self.root.glob("*.json"):
                if metadata.name.startswith("idem-"):
                    continue
                try:
                    existing = UploadSession(**json.loads(metadata.read_text()))
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if existing.user_id != user_id or existing.idempotency_key != idempotency_key:
                    continue
                if existing.filename != filename or existing.size != size:
                    raise UploadError("Idempotency key has different upload metadata", 409)
                with _upload_lock(existing.upload_id):
                    session = self._load(existing.upload_id, user_id)
                    # Write idempotency index for future lookups
                    self._write_idempotency_index(idem_path, session.upload_id)
                    return session

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
            # Write idempotency index for O(1) future lookups
            self._write_idempotency_index(idem_path, session.upload_id)
            return session

    def _write_idempotency_index(self, idem_path: Path, upload_id: str) -> None:
        """Write a small index file mapping idempotency key → upload_id."""
        try:
            tmp = idem_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"upload_id": upload_id}, separators=(",", ":")))
            tmp.chmod(0o600)
            os.replace(tmp, idem_path)
        except OSError:
            pass  # non-fatal — fallback to glob scan

    def status(self, *, upload_id: str, user_id: str) -> UploadSession:
        with _upload_lock(upload_id):
            return self._load(upload_id, user_id)

    def append(self, *, upload_id: str, user_id: str, offset: int, chunk: bytes) -> UploadSession:
        if not chunk:
            raise UploadError("Upload chunk is empty")
        if len(chunk) > self.config.max_upload_chunk_size:
            raise UploadError("Upload chunk exceeds server limit", 413)

        digest = hashlib.sha256(chunk).hexdigest()

        # Phase 4: acquire lock briefly for validation and offset reservation
        _, part = self._paths(upload_id)
        with _upload_lock(upload_id):
            session = self._load(upload_id, user_id)
            if session.job_id is not None:
                raise UploadError("Upload is already finalized", 409)
            if offset < 0 or offset >= session.size or offset % session.chunk_size != 0:
                raise UploadError("Upload offset is not chunk-aligned", 409)
            expected_length = min(session.chunk_size, session.size - offset)
            if len(chunk) != expected_length:
                raise UploadError(f"Upload chunk length mismatch; expected {expected_length}", 409)
            # Duplicate-chunk check (idempotent success or 409 for different content)
            existing = session.received_chunks.get(str(offset))
            if existing and "_pending" not in existing:
                if int(existing["length"]) != len(chunk) or existing["sha256"] != digest:
                    raise UploadError("Upload chunk differs from acknowledged content", 409)
                return session

            # Check in-flight set to prevent concurrent duplicate offset writes
            with self._in_flight_lock:
                in_flight = self._in_flight.get(upload_id)
                if in_flight and offset in in_flight:
                    raise UploadError("Chunk is being written by another request", 409)
                if in_flight is None:
                    in_flight = set()
                    self._in_flight[upload_id] = in_flight
                in_flight.add(offset)

        # Phase 4: write chunk bytes WITHOUT holding the upload lock
        # Safe because different chunks write to disjoint byte ranges,
        # and each call opens a fresh file handle.
        try:
            with part.open("r+b") as handle:
                handle.seek(offset)
                handle.write(chunk)

            # Phase 2: submit to group-commit batcher instead of per-chunk fsync.
            # The batcher will fsync the data file and journal, then release us.
            journal_record = json.dumps(
                {"offset": offset, "length": len(chunk), "sha256": digest},
                separators=(",", ":"),
            )
            pending = _PendingCommit()
            journal = self._journal_path(upload_id)
            self._batcher.submit(pending, part, journal, journal_record)

            # Block until the group-commit fsync completes.
            # "Acknowledged means durable" — we do NOT return HTTP 200 until this.
            pending.event.wait()
            if pending.error is not None:
                raise pending.error

            # Phase 4: re-acquire lock briefly for bookkeeping
            with _upload_lock(upload_id):
                session = self._load(upload_id, user_id)
                # The journal replay in _load already picked up our entry,
                # but update in-memory state for the response
                if str(offset) not in session.received_chunks:
                    session.received_chunks[str(offset)] = {
                        "length": len(chunk),
                        "sha256": digest,
                    }
                    session.refresh_contiguous_offset()
                session.updated_at = time.time()
                return session
        finally:
            # Always remove from in-flight set
            with self._in_flight_lock:
                in_flight = self._in_flight.get(upload_id)
                if in_flight is not None:
                    in_flight.discard(offset)
                    if not in_flight:
                        self._in_flight.pop(upload_id, None)

    def complete(self, *, upload_id: str, user_id: str, user_request: str | None = None):
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
                payload = {
                    "path": str(part),
                    "sha256": digest,
                    "size": session.size,
                    "move_input": True,
                    "original_filename": session.filename,
                }
                if user_request:
                    payload["user_request"] = user_request
                job = self.queue.submit(
                    "import",
                    project_id,
                    payload,
                    scoped_key,
                )
            except ValueError as exc:
                raise UploadError(str(exc), 409) from exc
            session.sha256 = digest
            session.job_id = job.job_id
            session.updated_at = time.time()
            self._save(session)  # full snapshot + journal compaction
            return job


@dataclass
class S3MultipartUploadSession:
    """Durable server-side control record for a client-to-S3 upload."""

    upload_id: str
    user_id: str
    idempotency_key: str
    project_id: str
    filename: str
    size: int
    sha256: str
    object_key: str
    s3_upload_id: str
    part_size: int
    total_parts: int
    created_at: float
    updated_at: float
    state: str = "uploading"
    job_id: str | None = None
    part_checksums: dict[str, str] = field(default_factory=dict)
    completed_parts: dict[str, dict[str, int | str]] = field(default_factory=dict)

    def public(self) -> dict:
        return {
            "upload_mode": "s3",
            "protocol": "s3_multipart_v1",
            "upload_id": self.upload_id,
            "project_id": self.project_id,
            "filename": self.filename,
            "size": self.size,
            "sha256": self.sha256,
            "part_size": self.part_size,
            "total_parts": self.total_parts,
            "state": self.state,
            "job_id": self.job_id,
            "completed_parts": [
                {
                    "part_number": int(number),
                    **details,
                }
                for number, details in sorted(
                    self.completed_parts.items(), key=lambda item: int(item[0])
                )
            ],
            "uploaded_bytes": sum(
                int(details["size"]) for details in self.completed_parts.values()
            ),
        }


class S3MultipartUploadService:
    """Coordinate direct, private S3 multipart uploads without proxying APK bytes."""

    def __init__(self, config, queue, *, store: ArtifactStore | None = None):
        self.config = config
        self.queue = queue
        self.store = store or ArtifactStore(config)
        self.root = Path(config.data_dir) / "uploads" / "s3-sessions"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.part_size = int(config.s3_upload_part_size)
        if self.part_size < 5 * 1024 * 1024:
            raise ValueError("s3_upload_part_size must be at least 5 MiB")

    def _metadata_path(self, upload_id: str) -> Path:
        if re.fullmatch(r"[a-f0-9]{32}", upload_id) is None:
            raise UploadError("Upload not found", 404)
        return self.root / f"{upload_id}.json"

    def _idempotency_path(self, user_id: str, idempotency_key: str) -> Path:
        lookup = hashlib.sha256(f"{user_id}:{idempotency_key}".encode()).hexdigest()[:32]
        return self.root / f"idem-{lookup}.json"

    def _save(self, session: S3MultipartUploadSession) -> None:
        metadata = self._metadata_path(session.upload_id)
        temporary = metadata.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(asdict(session), separators=(",", ":")))
        temporary.chmod(0o600)
        os.replace(temporary, metadata)

    def _load(self, upload_id: str, user_id: str) -> S3MultipartUploadSession:
        try:
            session = S3MultipartUploadSession(
                **json.loads(self._metadata_path(upload_id).read_text())
            )
        except (FileNotFoundError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise UploadError("Upload not found", 404) from exc
        if session.user_id != user_id or session.upload_id != upload_id:
            raise UploadError("Upload not found", 404)
        return session

    def _write_idempotency_index(self, path: Path, upload_id: str) -> None:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({"upload_id": upload_id}, separators=(",", ":")))
        temporary.chmod(0o600)
        os.replace(temporary, path)

    def _cleanup_expired(self) -> None:
        cutoff = time.time() - self.config.upload_session_ttl
        for metadata in self.root.glob("[a-f0-9]*.json"):
            try:
                raw = json.loads(metadata.read_text())
                session = S3MultipartUploadSession(**raw)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if session.updated_at >= cutoff:
                continue
            with _upload_lock(f"s3-{session.upload_id}"):
                if session.state == "uploading" and not session.job_id:
                    try:
                        self.store.abort_multipart_upload(
                            key=session.object_key,
                            upload_id=session.s3_upload_id,
                        )
                    except ArtifactStoreError:
                        # The S3 lifecycle rule is the final cleanup backstop.
                        continue
                metadata.unlink(missing_ok=True)
                self._idempotency_path(session.user_id, session.idempotency_key).unlink(
                    missing_ok=True
                )

    @staticmethod
    def _valid_part_checksum(value: str) -> bool:
        try:
            return len(base64.b64decode(value, validate=True)) == 32
        except (binascii.Error, ValueError):
            return False

    @staticmethod
    def _normalize_etag(value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r'"?[A-Fa-f0-9]{32}(?:-[0-9]+)?"?', value):
            raise UploadError("Invalid S3 part ETag")
        return value if value.startswith('"') else f'"{value}"'

    @staticmethod
    def _safe_filename(filename: str) -> str:
        return ResumableUploadService._safe_filename(filename)

    def begin(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        filename: str,
        size: int,
        sha256: str,
    ) -> S3MultipartUploadSession:
        if not self.store.enabled:
            raise UploadError("Direct S3 upload is unavailable", 503)
        if not idempotency_key or len(idempotency_key) > 256:
            raise UploadError("A valid Idempotency-Key is required")
        if size <= 0:
            raise UploadError("APK size must be positive")
        if size > self.config.max_upload_size:
            raise UploadError("APK exceeds upload limit", 413)
        sha256 = sha256.strip().lower()
        if re.fullmatch(r"[a-f0-9]{64}", sha256) is None:
            raise UploadError("A valid APK SHA-256 is required")
        filename = self._safe_filename(filename)
        total_parts = math.ceil(size / self.part_size)
        if total_parts > 10_000:
            raise UploadError("APK requires too many S3 multipart parts", 413)
        self._cleanup_expired()

        # Serialize only callers sharing this user-scoped idempotency key. This
        # prevents concurrent retries from creating orphaned multipart uploads.
        idempotency_lock = hashlib.sha256(f"{user_id}:{idempotency_key}".encode()).hexdigest()
        with _upload_lock(f"s3-idem-{idempotency_lock}"):
            return self._begin_locked(
                user_id=user_id,
                idempotency_key=idempotency_key,
                filename=filename,
                size=size,
                sha256=sha256,
                total_parts=total_parts,
            )

    def _begin_locked(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        filename: str,
        size: int,
        sha256: str,
        total_parts: int,
    ) -> S3MultipartUploadSession:
        idem_path = self._idempotency_path(user_id, idempotency_key)
        if idem_path.exists():
            try:
                existing_id = str(json.loads(idem_path.read_text())["upload_id"])
                with _upload_lock(f"s3-{existing_id}"):
                    existing = self._load(existing_id, user_id)
                if (
                    existing.filename != filename
                    or existing.size != size
                    or existing.sha256 != sha256
                ):
                    raise UploadError("Idempotency key has different upload metadata", 409)
                return existing
            except UploadError as exc:
                if exc.status_code != 404:
                    raise
                idem_path.unlink(missing_ok=True)
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                idem_path.unlink(missing_ok=True)

        upload_id = uuid4().hex
        project_id = uuid4().hex[:16]
        try:
            object_key, s3_upload_id = self.store.begin_multipart_original(
                user_id,
                project_id,
                sha256=sha256,
                size=size,
            )
        except ArtifactStoreError as exc:
            raise UploadError(str(exc), 502) from exc
        now = time.time()
        session = S3MultipartUploadSession(
            upload_id=upload_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            project_id=project_id,
            filename=filename,
            size=size,
            sha256=sha256,
            object_key=object_key,
            s3_upload_id=s3_upload_id,
            part_size=self.part_size,
            total_parts=total_parts,
            created_at=now,
            updated_at=now,
        )
        with _upload_lock(f"s3-{upload_id}"):
            self._save(session)
            self._write_idempotency_index(idem_path, upload_id)
        return session

    def status(self, *, upload_id: str, user_id: str) -> S3MultipartUploadSession:
        with _upload_lock(f"s3-{upload_id}"):
            return self._load(upload_id, user_id)

    def presign_parts(
        self,
        *,
        upload_id: str,
        user_id: str,
        parts: list[dict[str, int | str]],
    ) -> list[dict]:
        if not parts or len(parts) > 100:
            raise UploadError("Request between 1 and 100 upload parts")
        numbers = [int(part["part_number"]) for part in parts]
        if len(set(numbers)) != len(numbers):
            raise UploadError("Duplicate upload part number")
        with _upload_lock(f"s3-{upload_id}"):
            session = self._load(upload_id, user_id)
            if session.state != "uploading" or session.job_id:
                raise UploadError("Upload is already finalized", 409)
            for part in parts:
                number = int(part["part_number"])
                checksum = str(part["checksum_sha256"])
                if number < 1 or number > session.total_parts:
                    raise UploadError("Upload part number is out of range")
                if not self._valid_part_checksum(checksum):
                    raise UploadError("Invalid upload part SHA-256")
                known = session.part_checksums.get(str(number))
                if known is not None and known != checksum:
                    raise UploadError("Upload part checksum changed", 409)
                session.part_checksums[str(number)] = checksum
            session.updated_at = time.time()
            self._save(session)

            authorized = []
            for part in parts:
                number = int(part["part_number"])
                checksum = str(part["checksum_sha256"])
                try:
                    url = self.store.presign_multipart_part(
                        key=session.object_key,
                        upload_id=session.s3_upload_id,
                        part_number=number,
                        checksum_sha256=checksum,
                    )
                except ArtifactStoreError as exc:
                    raise UploadError(str(exc), 502) from exc
                authorized.append(
                    {
                        "part_number": number,
                        "url": url,
                        "headers": {"x-amz-checksum-sha256": checksum},
                        "expires_in": self.config.s3_presign_expiry,
                    }
                )
            return authorized

    def report_parts(
        self,
        *,
        upload_id: str,
        user_id: str,
        parts: list[dict[str, int | str]],
    ) -> S3MultipartUploadSession:
        if not parts or len(parts) > 100:
            raise UploadError("Report between 1 and 100 completed parts")
        numbers = [int(part["part_number"]) for part in parts]
        if len(set(numbers)) != len(numbers):
            raise UploadError("Duplicate completed part number")
        with _upload_lock(f"s3-{upload_id}"):
            session = self._load(upload_id, user_id)
            if session.state != "uploading" or session.job_id:
                raise UploadError("Upload is already finalized", 409)
            for part in parts:
                number = int(part["part_number"])
                checksum = str(part["checksum_sha256"])
                if number < 1 or number > session.total_parts:
                    raise UploadError("Upload part number is out of range")
                if session.part_checksums.get(str(number)) != checksum:
                    raise UploadError("Completed part checksum was not authorized", 409)
                expected_size = min(
                    session.part_size,
                    session.size - ((number - 1) * session.part_size),
                )
                size = int(part["size"])
                if size != expected_size:
                    raise UploadError(
                        f"Upload part {number} size mismatch; expected {expected_size}", 409
                    )
                details: dict[str, int | str] = {
                    "etag": self._normalize_etag(str(part["etag"])),
                    "checksum_sha256": checksum,
                    "size": size,
                }
                previous = session.completed_parts.get(str(number))
                if previous is not None and previous != details:
                    raise UploadError("Completed upload part changed", 409)
                session.completed_parts[str(number)] = details
            session.updated_at = time.time()
            self._save(session)
            return session

    def complete(self, *, upload_id: str, user_id: str, user_request: str | None = None):
        with _upload_lock(f"s3-{upload_id}"):
            session = self._load(upload_id, user_id)
            scoped_key = f"{user_id}:{session.idempotency_key}"
            if session.job_id:
                job = JobRepository().get(session.job_id)
                if job:
                    return job
            existing = JobRepository().find_by_idempotency(scoped_key, user_id=user_id)
            if existing:
                session.job_id = existing.job_id
                session.state = "queued"
                session.updated_at = time.time()
                self._save(session)
                return existing

            if session.state == "uploading":
                expected_numbers = {str(number) for number in range(1, session.total_parts + 1)}
                if set(session.completed_parts) != expected_numbers:
                    raise UploadError(
                        f"Upload is incomplete; expected {session.total_parts} completed parts",
                        409,
                    )
                s3_parts = [
                    {
                        "PartNumber": number,
                        "ETag": str(session.completed_parts[str(number)]["etag"]),
                        "ChecksumSHA256": str(
                            session.completed_parts[str(number)]["checksum_sha256"]
                        ),
                    }
                    for number in range(1, session.total_parts + 1)
                ]
                try:
                    self.store.complete_multipart_original(
                        key=session.object_key,
                        upload_id=session.s3_upload_id,
                        parts=s3_parts,
                    )
                except ArtifactStoreError as exc:
                    # Completion is not transactional with our local metadata. If
                    # S3 committed before the response was lost, HEAD proves the
                    # immutable object exists and the retry may continue safely.
                    try:
                        self.store.verify_original_object(
                            key=session.object_key,
                            expected_size=session.size,
                            expected_sha256=session.sha256,
                        )
                    except ArtifactStoreError:
                        raise UploadError(str(exc), 502) from exc
                session.state = "object_complete"
                session.updated_at = time.time()
                self._save(session)

            try:
                self.store.verify_original_object(
                    key=session.object_key,
                    expected_size=session.size,
                    expected_sha256=session.sha256,
                )
            except ArtifactStoreError as exc:
                raise UploadError(str(exc), 409) from exc

            access = AccessService()
            if not access.owns_project(user_id, session.project_id):
                try:
                    access.claim_project(user_id, session.project_id)
                except Exception as exc:
                    raise UploadError(
                        "Unable to reserve the private project workspace", 409
                    ) from exc
            payload = {
                "s3_object_key": session.object_key,
                "sha256": session.sha256,
                "size": session.size,
                "original_filename": session.filename,
                "durable_original": True,
            }
            if user_request:
                payload["user_request"] = user_request
            try:
                job = self.queue.submit(
                    "import",
                    session.project_id,
                    payload,
                    scoped_key,
                )
            except ValueError as exc:
                raise UploadError(str(exc), 409) from exc
            session.job_id = job.job_id
            session.state = "queued"
            session.updated_at = time.time()
            self._save(session)
            return job
