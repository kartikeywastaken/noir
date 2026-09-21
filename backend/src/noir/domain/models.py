"""NOIR domain models (Pydantic)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from noir.domain.enums import (
    ApprovalScope,
    ApprovalStatus,
    EventSeverity,
    JobState,
    PatchOperationType,
    ProjectStatus,
    Provenance,
    SigningProfileType,
    ValidationSeverity,
    WorkflowStage,
)


def _new_id() -> str:
    return uuid4().hex[:16]


def _now() -> datetime:
    return datetime.now(UTC)


# ── Project ──────────────────────────────────────────────────────────


class ProjectInfo(BaseModel):
    """Core project metadata."""

    id: str = Field(default_factory=_new_id)
    status: ProjectStatus = ProjectStatus.CREATED
    original_filename: str = ""
    original_size: int = 0
    original_sha256: str = ""
    package_name: str = ""
    version_name: str = ""
    version_code: str = ""
    authorization_acknowledged: bool = False
    authorization_timestamp: datetime | None = None
    workspace_revision: int = 0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    dirty: bool = False
    execution_profile: str = "full_decode"
    supported_operations: list[str] = Field(default_factory=list)
    compatibility_status: str = "unknown"
    compatibility_reasons: list[str] = Field(default_factory=list)
    payload_version: str | None = None


# ── Analysis ─────────────────────────────────────────────────────────


class ComponentInfo(BaseModel):
    """An Android manifest component."""

    name: str
    component_type: str  # activity, service, receiver, provider
    exported: bool | None = None
    permission: str | None = None
    intent_filters: list[dict[str, Any]] = Field(default_factory=list)
    is_launcher: bool = False
    is_alias: bool = False
    target_activity: str | None = None


class SmaliClassInfo(BaseModel):
    """Smali class descriptor."""

    descriptor: str
    file_path: str
    method_count: int = 0
    methods: list[str] = Field(default_factory=list)
    dex_index: int = 0


class NativeLibInfo(BaseModel):
    """Native library info."""

    abi: str
    libraries: list[str] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    """Complete static analysis result."""

    project_id: str
    package_name: str = ""
    version_name: str = ""
    version_code: str = ""
    min_sdk: int | None = None
    target_sdk: int | None = None
    application_class: str | None = None
    components: list[ComponentInfo] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    permission_definitions: list[dict[str, str]] = Field(default_factory=list)
    smali_classes: list[SmaliClassInfo] = Field(default_factory=list)
    smali_directories: list[str] = Field(default_factory=list)
    multidex: bool = False
    resource_directories: list[str] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)
    native_libs: list[NativeLibInfo] = Field(default_factory=list)
    native_abis: list[str] = Field(default_factory=list)
    runtimes: set[str] = Field(default_factory=set)
    runtime: str = "dalvik"  # backward compat; prefer runtimes set
    runtime_evidence: dict[str, list[str]] = Field(default_factory=dict)
    managed_assemblies: list[str] = Field(default_factory=list)
    il2cpp_metadata_files: list[str] = Field(default_factory=list)
    apktool_metadata: dict[str, Any] = Field(default_factory=dict)
    input_cert_info: dict[str, Any] = Field(default_factory=dict)
    obfuscation_indicators: list[str] = Field(default_factory=list)
    is_split_apk: bool = False
    compatibility_warnings: list[str] = Field(default_factory=list)
    metadata_sources: dict[str, str] = Field(default_factory=dict)
    analyzed_at: datetime = Field(default_factory=_now)

    @property
    def primary_runtime(self) -> str:
        """Single display label derived from the runtimes set.

        Priority: il2cpp > mono > flutter > hermes > react_native > native_only >
        hybrid_web > dalvik.
        Nothing should gate evidence collection on this property;
        use ``runtimes`` membership or direct evidence fields instead.
        """
        for candidate in (
            "il2cpp",
            "mono",
            "flutter",
            "hermes",
            "react_native",
            "native_only",
            "hybrid_web",
            "dalvik",
        ):
            if candidate in self.runtimes:
                return candidate
        return self.runtime  # backward compat with legacy serialized data


# ── File Manifest ────────────────────────────────────────────────────


class FileManifestEntry(BaseModel):
    """One entry in the workspace file manifest."""

    relative_path: str
    sha256: str
    size: int
    is_binary: bool = False


# ── Change Plan ──────────────────────────────────────────────────────


class PlanFileChange(BaseModel):
    """A planned file operation."""

    relative_path: str
    operation: PatchOperationType
    description: str = ""


class ChangePlan(BaseModel):
    """Structured change plan for review."""

    schema_version: str = "1.0"
    plan_id: str = Field(default_factory=_new_id)
    project_id: str
    workspace_revision: int
    user_request: str
    provider: str | None = None
    model: str | None = None
    intended_outcome: str = ""
    file_changes: list[PlanFileChange] = Field(default_factory=list)
    manifest_changes: list[str] = Field(default_factory=list)
    permission_changes: list[str] = Field(default_factory=list)
    component_changes: list[str] = Field(default_factory=list)
    smali_integration_points: list[str] = Field(default_factory=list)
    behavioral_changes: list[str] = Field(default_factory=list)
    network_destinations: list[str] = Field(default_factory=list)
    data_categories: list[str] = Field(default_factory=list)
    runtime_triggers: list[str] = Field(default_factory=list)
    background_behavior: list[str] = Field(default_factory=list)
    compatibility_concerns: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    validation_steps: list[str] = Field(default_factory=list)
    expected_test_results: list[str] = Field(default_factory=list)
    unsupported_aspects: list[str] = Field(default_factory=list)
    native_runtime: str | None = None
    native_runtimes: list[str] = Field(default_factory=list)
    binary_targets: list[str] = Field(default_factory=list)
    binary_risks: list[str] = Field(default_factory=list)
    discovery_transcript: list[dict[str, Any]] = Field(default_factory=list)
    discovery_api_calls: int = 0
    discovery_stop_reason: str = ""
    execution_mode: str = "ai"
    detected_intents: list[str] = Field(default_factory=list)
    patch_strategies: list[str] = Field(default_factory=list)
    runtime_configuration: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)

    def compute_hash(self) -> str:
        """Compute deterministic hash of plan content."""
        content = self.model_dump_json(exclude={"created_at"})
        return hashlib.sha256(content.encode()).hexdigest()


# ── Patch ────────────────────────────────────────────────────────────


class PatchOperation(BaseModel):
    """A single patch operation."""

    relative_path: str
    operation: PatchOperationType
    expected_preimage_hash: str | None = None
    expected_absent: bool = False
    match_content: str | None = None
    new_content: str | None = None
    new_content_base64: str | None = None
    class_descriptor: str | None = None
    method_signature: str | None = None
    anchor: str | None = None
    xml_element: str | None = None
    xml_attributes: dict[str, str] = Field(default_factory=dict)
    xml_match_attributes: dict[str, str] = Field(
        default_factory=dict,
        exclude_if=lambda value: not value,
    )
    xml_namespace: str | None = None
    assembly_name: str | None = None
    type_full_name: str | None = None
    new_il_source: str | None = None
    expected_method_il_hash: str | None = None
    field_name: str | None = None
    il2cpp_type_full_name: str | None = None
    il2cpp_method_signature: str | None = None
    il2cpp_return_constant: int | None = None
    expected_function_bytes_hash: str | None = None
    native_offset: int | None = None
    native_length: int | None = None
    native_new_bytes_hex: str | None = None
    native_redirect_target_offset: int | None = None
    native_abi: str | None = None
    expected_native_bytes_hash: str | None = None
    native_skipped_abis: list[str] = Field(default_factory=list)
    native_skip_reason: str | None = None
    affected_scope: str = ""


class PatchSet(BaseModel):
    """A set of patch operations."""

    patch_id: str = Field(default_factory=_new_id)
    plan_id: str
    project_id: str
    workspace_revision: int
    provenance: Provenance = Provenance.AI_GENERATED
    execution_mode: str = "ai"
    detected_intents: list[str] = Field(default_factory=list)
    patch_strategies: list[str] = Field(default_factory=list)
    runtime_configuration: dict[str, Any] = Field(default_factory=dict)
    operations: list[PatchOperation] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)

    def compute_hash(self) -> str:
        content = self.model_dump_json(exclude={"created_at"})
        return hashlib.sha256(content.encode()).hexdigest()


# ── Approval ─────────────────────────────────────────────────────────


class ApprovalRecord(BaseModel):
    """Approval binding."""

    approval_id: str = Field(default_factory=_new_id)
    project_id: str
    scope: ApprovalScope
    workspace_revision: int
    target_hash: str
    target_id: str
    status: ApprovalStatus = ApprovalStatus.APPROVED
    actor: str = "local_cli"
    risk_acknowledgments: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)


# ── Jobs ─────────────────────────────────────────────────────────────


class JobInfo(BaseModel):
    """Job tracking."""

    job_id: str = Field(default_factory=_new_id)
    project_id: str
    stage: WorkflowStage
    state: JobState = JobState.QUEUED
    attempt: int = 1
    max_attempts: int = 3
    error_message: str | None = None
    result_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None


# ── Events ───────────────────────────────────────────────────────────


class AuditEvent(BaseModel):
    """Structured audit event."""

    event_id: str = Field(default_factory=_new_id)
    project_id: str
    job_id: str | None = None
    stage: WorkflowStage | None = None
    severity: EventSeverity = EventSeverity.INFO
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_now)


# ── Signing ──────────────────────────────────────────────────────────


class SigningProfile(BaseModel):
    """Signing key profile reference (no secrets)."""

    name: str
    profile_type: SigningProfileType
    keystore_path: str | None = None
    key_alias: str | None = None
    certificate_fingerprint_sha256: str | None = None
    created_at: datetime = Field(default_factory=_now)


class SigningAuthorization(BaseModel):
    """Signing authorization binding."""

    authorization_id: str = Field(default_factory=_new_id)
    project_id: str
    build_id: str
    unsigned_artifact_hash: str
    signing_profile: str
    certificate_fingerprint: str = ""
    authorized_at: datetime = Field(default_factory=_now)


# ── Build ────────────────────────────────────────────────────────────


class BuildResult(BaseModel):
    """Build outcome."""

    build_id: str = Field(default_factory=_new_id)
    project_id: str
    workspace_revision: int
    unsigned_apk_path: str | None = None
    unsigned_apk_hash: str | None = None
    aligned_apk_path: str | None = None
    aligned_apk_hash: str | None = None
    signed_apk_path: str | None = None
    signed_apk_hash: str | None = None
    success: bool = False
    error_message: str | None = None
    apktool_version: str | None = None
    build_tools_version: str | None = None
    tool_logs: str = ""
    attempt_number: int = 1
    retryable: bool = False
    failure_info: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


# ── Validation ───────────────────────────────────────────────────────


class ValidationFinding(BaseModel):
    """A single validation finding."""

    check_name: str
    severity: ValidationSeverity
    message: str
    file_path: str | None = None
    line_number: int | None = None
    evidence: str | None = None


class ValidationResult(BaseModel):
    """Complete validation outcome."""

    validation_id: str = Field(default_factory=_new_id)
    project_id: str
    workspace_revision: int
    findings: list[ValidationFinding] = Field(default_factory=list)
    passed: bool = True
    error_count: int = 0
    warning_count: int = 0
    created_at: datetime = Field(default_factory=_now)

    def has_errors(self) -> bool:
        return any(f.severity == ValidationSeverity.ERROR for f in self.findings)


# ── Manual Edit Session ──────────────────────────────────────────────


class ManualEditSession(BaseModel):
    """Tracked manual edit session."""

    session_id: str = Field(default_factory=_new_id)
    project_id: str
    workspace_revision_start: int
    active: bool = True
    declared_files: list[str] = Field(default_factory=list)
    detected_changes: list[str] = Field(default_factory=list)
    message: str = ""
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None


# ── API Token ────────────────────────────────────────────────────────


class ApiToken(BaseModel):
    """API token (stores hash only)."""

    token_id: str = Field(default_factory=_new_id)
    token_hash: str
    name: str = "default"
    user_id: str = "local"
    created_at: datetime = Field(default_factory=_now)


# ── Process Execution ────────────────────────────────────────────────


class ProcessResult(BaseModel):
    """Result of a subprocess execution."""

    command: list[str]
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False
    duration_seconds: float = 0.0
    tool_name: str = ""
    tool_version: str = ""
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None


# ── Doctor ───────────────────────────────────────────────────────────


class ToolCheck(BaseModel):
    """Result of checking one tool."""

    name: str
    available: bool
    path: str | None = None
    version: str | None = None
    required_for: str = ""
    message: str = ""


class DoctorReport(BaseModel):
    """Complete toolchain doctor report."""

    checks: list[ToolCheck] = Field(default_factory=list)
    all_required_available: bool = False
    build_capable: bool = False
    ai_configured: bool = False
    device_capable: bool = False
    summary: str = ""
