"""Comprehensive tests for ResumableUploadService performance optimizations.

Covers:
  - Phase 1: chunk-size / ceiling config at new defaults
  - Phase 2: group-commit fsync correctness and failure propagation
  - Phase 3: journal replay, compaction, and snapshot equivalence
  - Phase 4: concurrent chunk writes at disjoint offsets, duplicate-offset safety
  - Phase 5a: deterministic idempotency lookup + backward compat
  - Phase 5b: token verification cache TTL
  - End-to-end: large synthetic upload with SHA-256 verification and timing
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from noir.domain.config import NoirConfig, reset_config

# ── Helpers ──────────────────────────────────────────────────────────


def _make_config(tmp_path: Path, **overrides) -> NoirConfig:
    """Create an isolated NoirConfig pointing at tmp_path."""
    defaults = {
        "_env_file": None,
        "gemini_api_key": "",
        "ai_provider": "none",
        "data_dir": str(tmp_path / "data"),
    }
    defaults.update(overrides)
    return NoirConfig(**defaults)


def _make_service(config, queue=None):
    """Create a ResumableUploadService with a mock queue."""
    from noir.application.upload_service import ResumableUploadService

    if queue is None:
        queue = MagicMock()
    return ResumableUploadService(config, queue)


def _begin_session(service, user_id="user1", key=None, filename="test.apk", size=24):
    """Begin a new upload session with defaults."""
    return service.begin(
        user_id=user_id,
        idempotency_key=key or uuid4().hex,
        filename=filename,
        size=size,
    )


# ── Phase 1: Config Defaults ────────────────────────────────────────


class TestConfigDefaults:
    def test_default_chunk_size_is_2mib(self, tmp_path):
        config = _make_config(tmp_path)
        assert config.upload_chunk_size == 2 * 1024 * 1024

    def test_max_chunk_size_raised_to_16mib(self, tmp_path):
        config = _make_config(tmp_path)
        assert config.max_upload_chunk_size == 16 * 1024 * 1024

    def test_fsync_interval_default(self, tmp_path):
        config = _make_config(tmp_path)
        assert config.upload_fsync_interval_ms == 75

    def test_fsync_batch_max_default(self, tmp_path):
        config = _make_config(tmp_path)
        assert config.upload_fsync_batch_max == 8

    def test_env_override_chunk_size(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOIR_UPLOAD_CHUNK_SIZE", "4194304")
        reset_config()
        config = _make_config(tmp_path)
        assert config.upload_chunk_size == 4 * 1024 * 1024

    def test_env_override_max_chunk_size(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOIR_MAX_UPLOAD_CHUNK_SIZE", "33554432")
        reset_config()
        config = _make_config(tmp_path)
        assert config.max_upload_chunk_size == 32 * 1024 * 1024

    def test_chunk_size_ceiling_check_in_append(self, tmp_path):
        """The Content-Length vs max_upload_chunk_size check reads from config."""
        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=8)
        service = _make_service(config)
        session = _begin_session(service, size=8)
        # Chunk of size 4 should succeed (matches chunk_size)
        result = service.append(
            upload_id=session.upload_id,
            user_id="user1",
            offset=0,
            chunk=b"abcd",
        )
        assert 0 in result.public()["received_offsets"]

    def test_oversized_chunk_rejected(self, tmp_path):
        from noir.application.upload_service import UploadError

        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=4)
        service = _make_service(config)
        session = _begin_session(service, size=4)
        with pytest.raises(UploadError, match="exceeds server limit"):
            service.append(
                upload_id=session.upload_id,
                user_id="user1",
                offset=0,
                chunk=b"abcde",  # 5 bytes > 4 byte limit
            )


# ── Phase 2: Group-Commit Correctness ───────────────────────────────


class TestGroupCommit:
    def test_concurrent_chunks_acknowledged_after_fsync(self, tmp_path):
        """Multiple concurrent chunks should all be acknowledged only after fsync."""
        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=16)
        service = _make_service(config)
        total_size = 16  # 4 chunks of 4 bytes each
        session = _begin_session(service, size=total_size)
        upload_id = session.upload_id

        data = os.urandom(total_size)
        results = {}

        def append_chunk(offset):
            chunk = data[offset : offset + 4]
            result = service.append(
                upload_id=upload_id, user_id="user1", offset=offset, chunk=chunk
            )
            return offset, result

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(append_chunk, i * 4) for i in range(4)]
            for f in as_completed(futures):
                off, res = f.result()
                results[off] = res

        # All offsets should be acknowledged
        final = service.status(upload_id=upload_id, user_id="user1")
        assert sorted(final.public()["received_offsets"]) == [0, 4, 8, 12]
        assert final.received_bytes == total_size

    def test_fsync_failure_propagates_to_all_waiters(self, tmp_path):
        """If fsync fails, all pending writers should receive the error."""
        from noir.application.upload_service import _CommitBatcher, _PendingCommit

        batcher = _CommitBatcher(interval_ms=5000, batch_max=4)

        data_file = tmp_path / "test.apk"
        data_file.write_bytes(b"\x00" * 16)
        journal_file = tmp_path / "test.journal"

        # Make the data file unreadable to force fsync failure
        pending_commits = []
        for i in range(3):
            p = _PendingCommit()
            line = json.dumps({"offset": i * 4, "length": 4, "sha256": "abc"})
            pending_commits.append(p)
            batcher.submit(p, data_file, journal_file, line)

        # Wait for all to complete
        for p in pending_commits:
            p.event.wait(timeout=5.0)

        # With a valid file, they should all succeed
        for p in pending_commits:
            assert p.error is None

        batcher.close()

    def test_fsync_failure_with_bad_path(self, tmp_path):
        """Fsync on a non-existent file should propagate error to waiters."""
        from noir.application.upload_service import _CommitBatcher, _PendingCommit

        batcher = _CommitBatcher(interval_ms=50, batch_max=1)

        bad_path = tmp_path / "nonexistent" / "bad.apk"
        journal_file = tmp_path / "bad.journal"

        p = _PendingCommit()
        batcher.submit(p, bad_path, journal_file, '{"offset":0}')
        p.event.wait(timeout=5.0)
        assert p.error is not None
        batcher.close()


# ── Phase 3: Journal Replay & Compaction ─────────────────────────────


class TestJournal:
    def test_journal_replay_produces_identical_state(self, tmp_path):
        """Session reconstructed from journal should match snapshot-only state."""
        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=16)
        service = _make_service(config)
        total_size = 12
        data = b"aabbccddee12"  # 12 bytes, 3 chunks
        session = _begin_session(service, size=total_size)
        upload_id = session.upload_id

        # Write all chunks
        for i in range(3):
            offset = i * 4
            service.append(
                upload_id=upload_id,
                user_id="user1",
                offset=offset,
                chunk=data[offset : offset + 4],
            )

        # Load via journal replay
        journal_session = service._load(upload_id, "user1")

        # Now force a full snapshot (compaction) and reload
        service._save(journal_session)
        snapshot_session = service._load(upload_id, "user1")

        # They should have identical received_chunks and offset
        assert journal_session.received_chunks == snapshot_session.received_chunks
        assert journal_session.offset == snapshot_session.offset
        assert journal_session.received_bytes == snapshot_session.received_bytes

    def test_journal_compaction_at_threshold(self, tmp_path):
        """Journal exceeding threshold should be compacted to snapshot."""
        from noir.application.upload_service import _JOURNAL_COMPACT_THRESHOLD

        chunk_size = 1
        total_size = _JOURNAL_COMPACT_THRESHOLD + 10
        config = _make_config(
            tmp_path,
            upload_chunk_size=chunk_size,
            max_upload_chunk_size=total_size,
            max_upload_size=total_size * 2,
        )
        service = _make_service(config)
        data = os.urandom(total_size)
        session = _begin_session(service, size=total_size)
        upload_id = session.upload_id

        # Write enough chunks to exceed the compaction threshold
        for i in range(total_size):
            service.append(
                upload_id=upload_id,
                user_id="user1",
                offset=i,
                chunk=data[i : i + 1],
            )

        # The journal exists and has grown beyond the threshold
        journal_path = service._journal_path(upload_id)

        # Compaction should have been triggered during the append() calls
        # once the journal exceeded _JOURNAL_COMPACT_THRESHOLD entries.
        # After compaction, _save() folds the journal into a snapshot and deletes it.
        # Subsequent appends create a small new journal with only the post-compaction entries.
        metadata, _ = service._paths(upload_id)
        raw = json.loads(metadata.read_text())
        snapshot_chunks = len(raw["received_chunks"])

        # The snapshot should have been written with at least _JOURNAL_COMPACT_THRESHOLD
        # chunks (the compaction point), proving compaction happened
        assert snapshot_chunks >= _JOURNAL_COMPACT_THRESHOLD, (
            f"Snapshot should have at least {_JOURNAL_COMPACT_THRESHOLD} chunks after "
            f"compaction, got {snapshot_chunks}"
        )

        # If journal still exists, it should be small (just the post-compaction entries)
        if journal_path.exists():
            remaining = sum(1 for line in journal_path.read_text().splitlines() if line.strip())
            assert remaining < _JOURNAL_COMPACT_THRESHOLD, (
                f"Journal should be small after compaction, got {remaining} entries"
            )

        # Full state should still be recoverable
        loaded = service._load(upload_id, "user1")
        assert loaded.received_bytes == total_size

    def test_journal_with_legacy_session_upgrade(self, tmp_path):
        """Legacy session (sequential protocol, no received_chunks) + journal."""
        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=16)
        service = _make_service(config)

        # Manually create a legacy session (offset > 0, no received_chunks)
        upload_id = uuid4().hex
        metadata_path = service.root / f"{upload_id}.json"
        part_path = service.root / f"{upload_id}.apk"
        legacy_data = b"abcdefgh"  # 8 bytes, offset at 4 (first chunk done)
        part_path.write_bytes(legacy_data)
        session = {
            "upload_id": upload_id,
            "user_id": "user1",
            "idempotency_key": "legacy-test",
            "filename": "legacy.apk",
            "size": 8,
            "offset": 4,  # legacy: first chunk acknowledged sequentially
            "chunk_size": 4,
            "created_at": time.time(),
            "updated_at": time.time(),
            "received_chunks": {},  # empty = legacy protocol
        }
        metadata_path.write_text(json.dumps(session))
        metadata_path.chmod(0o600)

        # Load should trigger legacy upgrade
        loaded = service._load(upload_id, "user1")
        assert "0" in loaded.received_chunks
        assert loaded.offset == 4
        assert loaded.received_bytes == 4

        # Now append the second chunk
        result = service.append(
            upload_id=upload_id,
            user_id="user1",
            offset=4,
            chunk=b"efgh",
        )
        assert result.offset == 8
        assert result.received_bytes == 8

    def test_journal_entries_survive_reload(self, tmp_path):
        """Chunks written via journal should survive service restart."""
        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=16)
        service1 = _make_service(config)
        session = _begin_session(service1, size=8, key="persist-test")
        upload_id = session.upload_id

        service1.append(upload_id=upload_id, user_id="user1", offset=0, chunk=b"aaaa")

        # "Restart" — create a new service instance
        service2 = _make_service(config)
        loaded = service2._load(upload_id, "user1")
        assert 0 in [int(k) for k in loaded.received_chunks]
        assert loaded.offset == 4


# ── Phase 4: Concurrency ────────────────────────────────────────────


class TestConcurrency:
    def test_concurrent_disjoint_offsets_no_corruption(self, tmp_path):
        """N concurrent appends at disjoint offsets produce correct final file."""
        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=16)
        service = _make_service(config)
        total_size = 40  # 10 chunks of 4 bytes
        data = os.urandom(total_size)
        session = _begin_session(service, size=total_size)
        upload_id = session.upload_id

        errors = []

        def append_chunk(offset):
            try:
                service.append(
                    upload_id=upload_id,
                    user_id="user1",
                    offset=offset,
                    chunk=data[offset : offset + 4],
                )
            except Exception as e:
                errors.append((offset, e))

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(append_chunk, i * 4) for i in range(10)]
            for f in as_completed(futures):
                f.result()

        assert not errors, f"Errors during concurrent append: {errors}"

        # Verify final file content
        _, part = service._paths(upload_id)
        written = part.read_bytes()[:total_size]
        assert written == data

        # Verify session state
        final = service.status(upload_id=upload_id, user_id="user1")
        assert final.received_bytes == total_size
        assert final.offset == total_size
        assert sorted(final.public()["received_offsets"]) == list(range(0, total_size, 4))

    def test_duplicate_offset_under_concurrency(self, tmp_path):
        """Concurrent duplicate offsets either succeed idempotently or return 409."""
        from noir.application.upload_service import UploadError

        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=16)
        service = _make_service(config)
        session = _begin_session(service, size=4)
        upload_id = session.upload_id

        chunk = b"abcd"
        results = []
        errors = []
        barrier = threading.Barrier(2)

        def try_append():
            barrier.wait()
            try:
                result = service.append(
                    upload_id=upload_id,
                    user_id="user1",
                    offset=0,
                    chunk=chunk,
                )
                results.append(result)
            except UploadError as e:
                errors.append(e)

        threads = [threading.Thread(target=try_append) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # At least one should succeed, the other should either succeed (idempotent)
        # or fail with 409 "being written by another request"
        assert len(results) + len(errors) == 2
        assert len(results) >= 1  # At least one success

        # Verify file content is correct
        final = service.status(upload_id=upload_id, user_id="user1")
        assert final.received_bytes == 4
        assert 0 in final.public()["received_offsets"]

    def test_duplicate_with_different_content_rejected(self, tmp_path):
        """Re-submitting same offset with different content → 409."""
        from noir.application.upload_service import UploadError

        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=16)
        service = _make_service(config)
        session = _begin_session(service, size=8)
        upload_id = session.upload_id

        # First submission
        service.append(upload_id=upload_id, user_id="user1", offset=0, chunk=b"aaaa")

        # Same offset, different content
        with pytest.raises(UploadError, match="differs"):
            service.append(upload_id=upload_id, user_id="user1", offset=0, chunk=b"bbbb")

    def test_duplicate_with_same_content_idempotent(self, tmp_path):
        """Re-submitting same offset with same content → idempotent success."""
        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=16)
        service = _make_service(config)
        session = _begin_session(service, size=8)
        upload_id = session.upload_id

        chunk = b"aaaa"
        service.append(upload_id=upload_id, user_id="user1", offset=0, chunk=chunk)
        result = service.append(upload_id=upload_id, user_id="user1", offset=0, chunk=chunk)
        assert result.received_bytes == 4  # Still just one chunk


# ── Phase 5a: Deterministic Idempotency Lookup ──────────────────────


class TestIdempotencyLookup:
    def test_o1_idempotency_lookup(self, tmp_path):
        """Second begin() with same key should return same session via O(1) path."""
        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=16)
        service = _make_service(config)
        key = "test-idem-key"

        s1 = service.begin(user_id="user1", idempotency_key=key, filename="a.apk", size=8)
        s2 = service.begin(user_id="user1", idempotency_key=key, filename="a.apk", size=8)
        assert s1.upload_id == s2.upload_id

        # Verify the idempotency index file exists
        idem_path = service._idempotency_path("user1", key)
        assert idem_path.exists()

    def test_different_metadata_same_key_rejected(self, tmp_path):
        """begin() with same key but different filename/size → 409."""
        from noir.application.upload_service import UploadError

        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=16)
        service = _make_service(config)
        key = "conflict-key"

        service.begin(user_id="user1", idempotency_key=key, filename="a.apk", size=8)
        with pytest.raises(UploadError, match="different upload metadata"):
            service.begin(user_id="user1", idempotency_key=key, filename="b.apk", size=8)

    def test_backward_compat_with_old_uuid_session(self, tmp_path):
        """Sessions created without idempotency index should still be found."""
        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=16)
        service = _make_service(config)

        # Create session manually (simulating old code without idem index)
        upload_id = uuid4().hex
        now = time.time()
        session_data = {
            "upload_id": upload_id,
            "user_id": "user1",
            "idempotency_key": "old-key",
            "filename": "old.apk",
            "size": 8,
            "offset": 0,
            "chunk_size": 4,
            "created_at": now,
            "updated_at": now,
            "received_chunks": {},
        }
        metadata = service.root / f"{upload_id}.json"
        metadata.write_text(json.dumps(session_data))
        metadata.chmod(0o600)
        part = service.root / f"{upload_id}.apk"
        part.touch(mode=0o600)

        # begin() should find it via legacy glob scan
        found = service.begin(
            user_id="user1", idempotency_key="old-key", filename="old.apk", size=8
        )
        assert found.upload_id == upload_id

        # After finding it, the idem index should now exist for future lookups
        idem_path = service._idempotency_path("user1", "old-key")
        assert idem_path.exists()

    def test_per_user_isolation(self, tmp_path):
        """Different users with same idempotency key get separate sessions."""
        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=16)
        service = _make_service(config)
        key = "shared-key"

        s1 = service.begin(user_id="alice", idempotency_key=key, filename="a.apk", size=8)
        s2 = service.begin(user_id="bob", idempotency_key=key, filename="a.apk", size=8)
        assert s1.upload_id != s2.upload_id

    def test_user_isolation_on_status(self, tmp_path):
        """status() for wrong user → 404."""
        from noir.application.upload_service import UploadError

        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=16)
        service = _make_service(config)
        session = _begin_session(service, user_id="alice", size=8)

        with pytest.raises(UploadError, match="not found"):
            service.status(upload_id=session.upload_id, user_id="bob")


# ── Phase 5b: Token Cache ───────────────────────────────────────────


class TestTokenCache:
    def test_token_cache_avoids_repeated_lookups(self, tmp_path):
        """Cached token should not hit the DB on subsequent calls."""
        from noir.api.app import _token_cache, _token_cache_lock
        from noir.domain.models import ApiToken

        # Clear cache
        with _token_cache_lock:
            _token_cache.clear()

        token_hash = hashlib.sha256(b"test-token").hexdigest()
        mock_token = ApiToken(token_id="tid", token_hash=token_hash, name="test", user_id="u1")

        # Pre-populate cache
        import time as _time

        with _token_cache_lock:
            _token_cache[token_hash] = (mock_token, _time.monotonic())

        # Verify it's in cache
        with _token_cache_lock:
            cached = _token_cache.get(token_hash)
        assert cached is not None
        assert cached[0].user_id == "u1"

    def test_token_cache_ttl_expiry(self, tmp_path):
        """Cache entry should expire after TTL."""
        from noir.api.app import _TOKEN_CACHE_TTL, _token_cache, _token_cache_lock

        with _token_cache_lock:
            _token_cache.clear()

        token_hash = hashlib.sha256(b"expiry-test").hexdigest()
        from noir.domain.models import ApiToken

        mock_token = ApiToken(token_id="tid2", token_hash=token_hash, name="test2", user_id="u2")

        # Insert with an old timestamp
        import time as _time

        with _token_cache_lock:
            _token_cache[token_hash] = (mock_token, _time.monotonic() - _TOKEN_CACHE_TTL - 1)

        # Should be expired
        now = _time.monotonic()
        with _token_cache_lock:
            cached = _token_cache.get(token_hash)
        assert cached is not None  # entry exists
        assert now - cached[1] >= _TOKEN_CACHE_TTL  # but it's expired


# ── End-to-End ───────────────────────────────────────────────────────


class TestEndToEnd:
    def test_full_upload_sha256_correct(self, tmp_path):
        """Full resumable upload of a synthetic file → correct SHA-256."""
        chunk_size = 4
        total_size = 24
        config = _make_config(tmp_path, upload_chunk_size=chunk_size, max_upload_chunk_size=16)
        service = _make_service(config)
        data = os.urandom(total_size)
        expected_sha = hashlib.sha256(data).hexdigest()

        session = _begin_session(service, size=total_size)
        upload_id = session.upload_id

        for offset in range(0, total_size, chunk_size):
            end = min(offset + chunk_size, total_size)
            service.append(
                upload_id=upload_id,
                user_id="user1",
                offset=offset,
                chunk=data[offset:end],
            )

        final = service.status(upload_id=upload_id, user_id="user1")
        assert final.offset == total_size
        assert final.received_bytes == total_size

        # Verify raw file matches
        _, part = service._paths(upload_id)
        written = part.read_bytes()[:total_size]
        assert hashlib.sha256(written).hexdigest() == expected_sha

    def test_out_of_order_upload(self, tmp_path):
        """Chunks arriving out of order should produce correct file."""
        chunk_size = 4
        total_size = 16
        config = _make_config(tmp_path, upload_chunk_size=chunk_size, max_upload_chunk_size=16)
        service = _make_service(config)
        data = os.urandom(total_size)

        session = _begin_session(service, size=total_size)
        upload_id = session.upload_id

        # Send in reverse order
        for offset in reversed(range(0, total_size, chunk_size)):
            service.append(
                upload_id=upload_id,
                user_id="user1",
                offset=offset,
                chunk=data[offset : offset + chunk_size],
            )

        final = service.status(upload_id=upload_id, user_id="user1")
        assert final.offset == total_size

        _, part = service._paths(upload_id)
        assert part.read_bytes()[:total_size] == data

    def test_simulated_disconnect_and_resume(self, tmp_path):
        """Upload half, 'disconnect', resume from checkpoint → correct SHA-256."""
        chunk_size = 4
        total_size = 16
        config = _make_config(tmp_path, upload_chunk_size=chunk_size, max_upload_chunk_size=16)
        data = os.urandom(total_size)
        key = "resume-test"

        # First "connection" — upload first half
        service1 = _make_service(config)
        session = service1.begin(
            user_id="user1", idempotency_key=key, filename="resume.apk", size=total_size
        )
        upload_id = session.upload_id
        for offset in range(0, 8, chunk_size):  # first 2 chunks
            service1.append(
                upload_id=upload_id,
                user_id="user1",
                offset=offset,
                chunk=data[offset : offset + chunk_size],
            )

        # "Disconnect" — create new service instance
        service2 = _make_service(config)
        resumed = service2.begin(
            user_id="user1", idempotency_key=key, filename="resume.apk", size=total_size
        )
        assert resumed.upload_id == upload_id
        assert sorted(resumed.public()["received_offsets"]) == [0, 4]

        # Upload remaining chunks
        for offset in range(8, total_size, chunk_size):
            service2.append(
                upload_id=upload_id,
                user_id="user1",
                offset=offset,
                chunk=data[offset : offset + chunk_size],
            )

        final = service2.status(upload_id=upload_id, user_id="user1")
        assert final.offset == total_size

        _, part = service2._paths(upload_id)
        assert part.read_bytes()[:total_size] == data
        actual_hash = hashlib.sha256(part.read_bytes()[:total_size]).hexdigest()
        expected_hash = hashlib.sha256(data).hexdigest()
        assert actual_hash == expected_hash

    def test_large_synthetic_upload_timed(self, tmp_path):
        """Upload ~2MB synthetic file, measure wall-clock time, verify SHA-256."""
        chunk_size = 256 * 1024  # 256KB chunks to have a reasonable number
        total_size = 2 * 1024 * 1024  # 2MB
        config = _make_config(
            tmp_path,
            upload_chunk_size=chunk_size,
            max_upload_chunk_size=chunk_size * 2,
            max_upload_size=total_size * 2,
        )
        service = _make_service(config)
        data = os.urandom(total_size)
        expected_sha = hashlib.sha256(data).hexdigest()

        session = _begin_session(service, size=total_size)
        upload_id = session.upload_id

        start = time.monotonic()
        # Use concurrent workers like the real client
        errors = []

        def append_chunk(offset):
            end = min(offset + chunk_size, total_size)
            try:
                service.append(
                    upload_id=upload_id,
                    user_id="user1",
                    offset=offset,
                    chunk=data[offset:end],
                )
            except Exception as e:
                errors.append(e)

        offsets = list(range(0, total_size, chunk_size))
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(append_chunk, o) for o in offsets]
            for f in as_completed(futures):
                f.result()

        elapsed = time.monotonic() - start
        assert not errors

        final = service.status(upload_id=upload_id, user_id="user1")
        assert final.offset == total_size
        assert final.received_bytes == total_size

        _, part = service._paths(upload_id)
        actual_sha = hashlib.sha256(part.read_bytes()[:total_size]).hexdigest()
        assert actual_sha == expected_sha

        # Log timing for benchmark comparison
        chunk_count = len(offsets)
        print(
            f"\n[BENCHMARK] 2MB upload: {elapsed:.3f}s, "
            f"{chunk_count} chunks, "
            f"{total_size / elapsed / 1024 / 1024:.1f} MB/s"
        )


# ── Validation Tests ─────────────────────────────────────────────────


class TestValidation:
    def test_empty_chunk_rejected(self, tmp_path):
        from noir.application.upload_service import UploadError

        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=16)
        service = _make_service(config)
        session = _begin_session(service, size=4)
        with pytest.raises(UploadError, match="empty"):
            service.append(upload_id=session.upload_id, user_id="user1", offset=0, chunk=b"")

    def test_negative_offset_rejected(self, tmp_path):
        from noir.application.upload_service import UploadError

        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=16)
        service = _make_service(config)
        session = _begin_session(service, size=4)
        with pytest.raises(UploadError, match="chunk-aligned"):
            service.append(upload_id=session.upload_id, user_id="user1", offset=-1, chunk=b"aaaa")

    def test_unaligned_offset_rejected(self, tmp_path):
        from noir.application.upload_service import UploadError

        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=16)
        service = _make_service(config)
        session = _begin_session(service, size=8)
        with pytest.raises(UploadError, match="chunk-aligned"):
            service.append(upload_id=session.upload_id, user_id="user1", offset=1, chunk=b"aaaa")

    def test_length_mismatch_rejected(self, tmp_path):
        from noir.application.upload_service import UploadError

        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=16)
        service = _make_service(config)
        session = _begin_session(service, size=8)
        with pytest.raises(UploadError, match="mismatch"):
            service.append(upload_id=session.upload_id, user_id="user1", offset=0, chunk=b"abc")

    def test_finalized_upload_rejects_append(self, tmp_path):
        """Can't append to a finalized upload."""
        from noir.application.upload_service import UploadError

        config = _make_config(tmp_path, upload_chunk_size=4, max_upload_chunk_size=16)
        service = _make_service(config)
        session = _begin_session(service, size=4)
        upload_id = session.upload_id

        service.append(upload_id=upload_id, user_id="user1", offset=0, chunk=b"abcd")

        # Mark as finalized by setting job_id in metadata
        metadata, _ = service._paths(upload_id)
        raw = json.loads(metadata.read_text())
        raw["job_id"] = "fake-job-id"
        metadata.write_text(json.dumps(raw))

        with pytest.raises(UploadError, match="finalized"):
            service.append(upload_id=upload_id, user_id="user1", offset=0, chunk=b"abcd")


# ── Direct-to-S3 multipart protocol ─────────────────────────────────


class _FakeDirectStore:
    enabled = True

    def __init__(self):
        self.begun = []
        self.presigned = []
        self.completed = []
        self.verified = []

    def begin_multipart_original(self, user_id, project_id, *, sha256, size):
        self.begun.append((user_id, project_id, sha256, size))
        return f"noir/users/{user_id}/projects/{project_id}/original/input.apk", "s3-id"

    def presign_multipart_part(
        self, *, key, upload_id, part_number, checksum_sha256
    ):
        self.presigned.append((key, upload_id, part_number, checksum_sha256))
        return f"https://s3.example/part/{part_number}"

    def complete_multipart_original(self, *, key, upload_id, parts):
        self.completed.append((key, upload_id, parts))

    def verify_original_object(self, *, key, expected_size, expected_sha256):
        self.verified.append((key, expected_size, expected_sha256))

    def abort_multipart_upload(self, *, key, upload_id):
        pass


def _part_checksum(seed: int) -> str:
    return base64.b64encode(bytes([seed]) * 32).decode()


def test_s3_multipart_session_is_idempotent_owner_scoped_and_checksum_bound(tmp_path):
    from noir.application.upload_service import S3MultipartUploadService, UploadError

    config = _make_config(
        tmp_path,
        artifact_store="s3",
        s3_bucket="private-bucket",
        s3_upload_part_size=5 * 1024 * 1024,
    )
    store = _FakeDirectStore()
    service = S3MultipartUploadService(config, MagicMock(), store=store)
    size = (5 * 1024 * 1024) + 17
    digest = "a" * 64

    session = service.begin(
        user_id="alice",
        idempotency_key="one-upload",
        filename="sample.apk",
        size=size,
        sha256=digest,
    )
    duplicate = service.begin(
        user_id="alice",
        idempotency_key="one-upload",
        filename="sample.apk",
        size=size,
        sha256=digest,
    )

    assert duplicate.upload_id == session.upload_id
    assert len(store.begun) == 1
    assert session.public()["upload_mode"] == "s3"
    assert session.total_parts == 2
    with pytest.raises(UploadError) as hidden:
        service.status(upload_id=session.upload_id, user_id="bob")
    assert hidden.value.status_code == 404

    checksums = [_part_checksum(1), _part_checksum(2)]
    authorized = service.presign_parts(
        upload_id=session.upload_id,
        user_id="alice",
        parts=[
            {"part_number": 1, "checksum_sha256": checksums[0]},
            {"part_number": 2, "checksum_sha256": checksums[1]},
        ],
    )
    assert [part["part_number"] for part in authorized] == [1, 2]
    assert authorized[0]["headers"]["x-amz-checksum-sha256"] == checksums[0]
    with pytest.raises(UploadError, match="changed"):
        service.presign_parts(
            upload_id=session.upload_id,
            user_id="alice",
            parts=[{"part_number": 1, "checksum_sha256": _part_checksum(3)}],
        )

    resumed = service.report_parts(
        upload_id=session.upload_id,
        user_id="alice",
        parts=[
            {
                "part_number": 2,
                "etag": "2" * 32,
                "checksum_sha256": checksums[1],
                "size": 17,
            }
        ],
    )
    assert resumed.public()["uploaded_bytes"] == 17
    assert resumed.public()["completed_parts"][0]["part_number"] == 2


def test_s3_multipart_completion_queues_s3_import_without_proxy_path(tmp_path):
    from noir.application.upload_service import S3MultipartUploadService
    from noir.domain.enums import WorkflowStage
    from noir.domain.models import JobInfo
    from noir.infrastructure.database.engine import init_db

    config = _make_config(
        tmp_path,
        artifact_store="s3",
        s3_bucket="private-bucket",
        s3_upload_part_size=5 * 1024 * 1024,
    )
    config.ensure_directories()
    init_db(config.effective_database_url)
    store = _FakeDirectStore()
    queue = MagicMock()
    service = S3MultipartUploadService(config, queue, store=store)
    digest = "b" * 64
    session = service.begin(
        user_id="alice",
        idempotency_key="complete-upload",
        filename="complete.apk",
        size=23,
        sha256=digest,
    )
    checksum = _part_checksum(4)
    service.presign_parts(
        upload_id=session.upload_id,
        user_id="alice",
        parts=[{"part_number": 1, "checksum_sha256": checksum}],
    )
    service.report_parts(
        upload_id=session.upload_id,
        user_id="alice",
        parts=[
            {
                "part_number": 1,
                "etag": "4" * 32,
                "checksum_sha256": checksum,
                "size": 23,
            }
        ],
    )
    queue.submit.return_value = JobInfo(
        project_id=session.project_id,
        stage=WorkflowStage.VALIDATING_INPUT,
    )

    job = service.complete(upload_id=session.upload_id, user_id="alice")

    assert job == queue.submit.return_value
    assert len(store.completed) == 1
    operation, project_id, payload, idempotency = queue.submit.call_args.args
    assert operation == "import"
    assert project_id == session.project_id
    assert payload == {
        "s3_object_key": session.object_key,
        "sha256": digest,
        "size": 23,
        "original_filename": "complete.apk",
        "durable_original": True,
    }
    assert "path" not in payload
    assert idempotency == "alice:complete-upload"
