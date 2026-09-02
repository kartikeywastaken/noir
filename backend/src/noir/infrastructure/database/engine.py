"""NOIR database engine and session management using SQLAlchemy + SQLite."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""

    pass


# ── ORM Models ───────────────────────────────────────────────────────


class ProjectRow(Base):
    __tablename__ = "projects"
    id = Column(String(32), primary_key=True)
    status = Column(String(32), nullable=False, default="created")
    original_filename = Column(String(512), default="")
    original_size = Column(Integer, default=0)
    original_sha256 = Column(String(64), default="")
    package_name = Column(String(256), default="")
    version_name = Column(String(128), default="")
    version_code = Column(String(32), default="")
    authorization_acknowledged = Column(Boolean, default=False)
    authorization_timestamp = Column(DateTime, nullable=True)
    workspace_revision = Column(Integer, default=0)
    dirty = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC))


class JobRow(Base):
    __tablename__ = "jobs"
    job_id = Column(String(32), primary_key=True)
    project_id = Column(String(32), nullable=False, index=True)
    stage = Column(String(64), nullable=False)
    state = Column(String(32), nullable=False, default="queued")
    attempt = Column(Integer, default=1)
    max_attempts = Column(Integer, default=3)
    error_message = Column(Text, nullable=True)
    result_data = Column(JSON, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC))
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class EventRow(Base):
    __tablename__ = "events"
    event_id = Column(String(32), primary_key=True)
    project_id = Column(String(32), nullable=False, index=True)
    job_id = Column(String(32), nullable=True, index=True)
    stage = Column(String(64), nullable=True)
    severity = Column(String(16), default="info")
    message = Column(Text, nullable=False)
    metadata_json = Column(Text, default="{}")
    timestamp = Column(DateTime, default=lambda: datetime.now(UTC))


class ApprovalRow(Base):
    __tablename__ = "approvals"
    approval_id = Column(String(32), primary_key=True)
    project_id = Column(String(32), nullable=False, index=True)
    scope = Column(String(32), nullable=False)
    workspace_revision = Column(Integer, nullable=False)
    target_hash = Column(String(64), nullable=False)
    target_id = Column(String(32), nullable=False)
    status = Column(String(16), default="approved")
    actor = Column(String(64), default="local_cli")
    risk_acknowledgments = Column(JSON, default=list)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


class PlanRow(Base):
    __tablename__ = "plans"
    plan_id = Column(String(32), primary_key=True)
    project_id = Column(String(32), nullable=False, index=True)
    workspace_revision = Column(Integer, nullable=False)
    user_request = Column(Text, default="")
    plan_json = Column(Text, nullable=False)
    plan_hash = Column(String(64), nullable=False)
    provider = Column(String(64), nullable=True)
    model = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


class PatchRow(Base):
    __tablename__ = "patches"
    patch_id = Column(String(32), primary_key=True)
    plan_id = Column(String(32), nullable=False, index=True)
    project_id = Column(String(32), nullable=False, index=True)
    workspace_revision = Column(Integer, nullable=False)
    provenance = Column(String(32), default="ai_generated")
    patch_json = Column(Text, nullable=False)
    patch_hash = Column(String(64), nullable=False)
    applied = Column(Boolean, default=False)
    undone = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


class BuildRow(Base):
    __tablename__ = "builds"
    build_id = Column(String(32), primary_key=True)
    project_id = Column(String(32), nullable=False, index=True)
    workspace_revision = Column(Integer, nullable=False)
    unsigned_apk_path = Column(String(512), nullable=True)
    unsigned_apk_hash = Column(String(64), nullable=True)
    aligned_apk_path = Column(String(512), nullable=True)
    aligned_apk_hash = Column(String(64), nullable=True)
    signed_apk_path = Column(String(512), nullable=True)
    signed_apk_hash = Column(String(64), nullable=True)
    success = Column(Boolean, default=False)
    error_message = Column(Text, nullable=True)
    apktool_version = Column(String(32), nullable=True)
    build_tools_version = Column(String(32), nullable=True)
    tool_logs = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


class SigningProfileRow(Base):
    __tablename__ = "signing_profiles"
    name = Column(String(64), primary_key=True)
    profile_type = Column(String(32), nullable=False)
    keystore_path = Column(String(512), nullable=True)
    key_alias = Column(String(128), nullable=True)
    certificate_fingerprint_sha256 = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


class ManualSessionRow(Base):
    __tablename__ = "manual_sessions"
    session_id = Column(String(32), primary_key=True)
    project_id = Column(String(32), nullable=False, index=True)
    workspace_revision_start = Column(Integer, nullable=False)
    active = Column(Boolean, default=True)
    declared_files = Column(JSON, default=list)
    detected_changes = Column(JSON, default=list)
    message = Column(Text, default="")
    started_at = Column(DateTime, default=lambda: datetime.now(UTC))
    finished_at = Column(DateTime, nullable=True)


class AnalysisRow(Base):
    __tablename__ = "analyses"
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String(32), nullable=False, index=True, unique=True)
    analysis_json = Column(Text, nullable=False)
    analyzed_at = Column(DateTime, default=lambda: datetime.now(UTC))


class TokenRow(Base):
    __tablename__ = "api_tokens"
    token_id = Column(String(32), primary_key=True)
    token_hash = Column(String(128), nullable=False)
    name = Column(String(64), default="default")
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


class UserRow(Base):
    __tablename__ = "workspace_users"
    user_id = Column(String(32), primary_key=True)
    name = Column(String(80), nullable=False)
    disabled = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


class TokenAccessRow(Base):
    __tablename__ = "token_access"
    token_id = Column(String(32), primary_key=True)
    user_id = Column(String(32), nullable=False, index=True)
    revoked = Column(Boolean, nullable=False, default=False)


class InviteRow(Base):
    __tablename__ = "workspace_invites"
    invite_id = Column(String(32), primary_key=True)
    code_hash = Column(String(64), nullable=False, unique=True)
    user_id = Column(String(32), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    redeemed_at = Column(DateTime, nullable=True)
    revoked = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


class ProjectAccessRow(Base):
    __tablename__ = "project_access"
    project_id = Column(String(32), primary_key=True)
    user_id = Column(String(32), nullable=False, index=True)


class SigningAccessRow(Base):
    __tablename__ = "signing_access"
    profile_name = Column(String(64), primary_key=True)
    user_id = Column(String(32), nullable=False, index=True)


class FileManifestRow(Base):
    __tablename__ = "file_manifests"
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String(32), nullable=False, index=True)
    workspace_revision = Column(Integer, nullable=False)
    manifest_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


class ValidationRow(Base):
    __tablename__ = "validations"
    validation_id = Column(String(32), primary_key=True)
    project_id = Column(String(32), nullable=False, index=True)
    workspace_revision = Column(Integer, nullable=False)
    findings_json = Column(Text, nullable=False)
    passed = Column(Boolean, default=True)
    error_count = Column(Integer, default=0)
    warning_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


# ── Engine Setup ─────────────────────────────────────────────────────


_engine = None
_SessionLocal = None
_database_url = None
_init_lock = threading.Lock()


def _enable_wal(dbapi_conn, connection_record):
    """Enable WAL mode for SQLite for better concurrency."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db(database_url: str) -> None:
    """Initialize the database engine and create tables."""
    global _engine, _SessionLocal, _database_url
    with _init_lock:
        if _engine is not None and _database_url == database_url:
            return
        if _engine is not None:
            _engine.dispose()
        _engine = create_engine(
            database_url, echo=False, connect_args={"check_same_thread": False, "timeout": 30}
        )
        event.listen(_engine, "connect", _enable_wal)
        Base.metadata.create_all(_engine)
        # Additive, transactional migration. Existing CLI projects/tokens belong
        # only to the local owner; never expose them to newly invited users.
        with _engine.begin() as connection:
            for statement in (
                "CREATE INDEX IF NOT EXISTS ix_jobs_state_created ON jobs (state, created_at)",
                "CREATE INDEX IF NOT EXISTS ix_jobs_project_state ON jobs (project_id, state)",
                "CREATE INDEX IF NOT EXISTS ix_events_job_time "
                "ON events (job_id, timestamp, event_id)",
                "CREATE INDEX IF NOT EXISTS ix_builds_project_revision "
                "ON builds (project_id, workspace_revision)",
            ):
                connection.execute(text(statement))
            connection.execute(
                text(
                    "INSERT OR IGNORE INTO workspace_users (user_id, name, disabled, created_at) "
                    "VALUES ('local', 'Owner', 0, CURRENT_TIMESTAMP)"
                )
            )
            for target, column, source, source_column in (
                ("project_access", "project_id", "projects", "id"),
                ("project_access", "project_id", "jobs", "project_id"),
                ("signing_access", "profile_name", "signing_profiles", "name"),
            ):
                connection.execute(
                    text(
                        f"INSERT OR IGNORE INTO {target} ({column}, user_id) "  # noqa: S608 -- fixed table names above
                        f"SELECT {source_column}, 'local' FROM {source}"
                    )
                )
            connection.execute(
                text(
                    "INSERT OR IGNORE INTO token_access (token_id, user_id, revoked) "
                    "SELECT token_id, 'local', 0 FROM api_tokens"
                )
            )
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
        _database_url = database_url


def get_session() -> Session:
    """Get a new database session."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _SessionLocal()


def get_engine():
    """Get the database engine."""
    return _engine
