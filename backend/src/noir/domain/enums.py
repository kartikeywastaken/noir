"""NOIR domain enumerations."""

from enum import Enum, StrEnum


class JobState(StrEnum):
    """Job lifecycle states."""

    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class WorkflowStage(StrEnum):
    """Stages within a workflow job."""

    VALIDATING_INPUT = "validating_input"
    DECODING = "decoding"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    GENERATING_PATCH = "generating_patch"
    APPLYING_PATCH = "applying_patch"
    VALIDATING_WORKSPACE = "validating_workspace"
    REBUILDING = "rebuilding"
    ALIGNING = "aligning"
    SIGNING = "signing"
    VERIFYING = "verifying"
    TESTING = "testing"
    REPORTING = "reporting"
    EXPORTING = "exporting"


class PatchOperationType(StrEnum):
    """Types of patch operations."""

    CREATE_FILE = "create_file"
    REPLACE_FILE = "replace_file"
    REPLACE_BLOCK = "replace_block"
    DELETE_FILE = "delete_file"
    MANIFEST_ADD = "manifest_add"
    MANIFEST_UPDATE = "manifest_update"
    MANIFEST_REMOVE = "manifest_remove"
    XML_RESOURCE_ADD = "xml_resource_add"
    XML_RESOURCE_UPDATE = "xml_resource_update"
    XML_RESOURCE_REMOVE = "xml_resource_remove"
    SMALI_REPLACE_METHOD = "smali_replace_method"
    SMALI_INSERT_AT_ANCHOR = "smali_insert_at_anchor"


class Provenance(StrEnum):
    """Origin of a change."""

    AI_GENERATED = "ai_generated"
    MANUAL = "manual"
    SYSTEM_GENERATED = "system_generated"


class ApprovalScope(StrEnum):
    """What an approval authorizes."""

    PLAN = "plan"
    PATCH = "patch"
    SIGNING = "signing"
    INSTALL = "install"
    REPLACEMENT = "replacement"


class ApprovalStatus(StrEnum):
    """Approval decision."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    STALE = "stale"


class SigningProfileType(StrEnum):
    """Types of signing profiles."""

    EPHEMERAL_DEBUG = "ephemeral_debug"
    PERSISTENT_LOCAL = "persistent_local"
    USER_SUPPLIED = "user_supplied"


class EventSeverity(StrEnum):
    """Event log severity levels."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ValidationSeverity(StrEnum):
    """Severity of a validation finding."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    SKIPPED = "skipped"
    UNSUPPORTED = "unsupported"


class ProjectStatus(StrEnum):
    """Project lifecycle status."""

    CREATED = "created"
    IMPORTING = "importing"
    DECODED = "decoded"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    MODIFYING = "modifying"
    VALIDATED = "validated"
    BUILDING = "building"
    BUILT = "built"
    SIGNED = "signed"
    EXPORTED = "exported"
    FAILED = "failed"


class RunnerMode(StrEnum):
    """Process isolation modes."""

    NATIVE = "native"
    CONTAINER = "container"


class AiProviderType(StrEnum):
    """Supported AI providers."""

    GEMINI = "gemini"
    NONE = "none"


class CapabilityLevel(StrEnum):
    """Toolchain capability classification."""

    REQUIRED_IMPORT = "required_for_import"
    REQUIRED_BUILD = "required_for_build"
    OPTIONAL_AI = "optional_for_ai"
    OPTIONAL_DEVICE = "optional_for_device"


class ExitCode(int, Enum):
    """CLI exit codes."""

    SUCCESS = 0
    INVALID_INPUT = 1
    MISSING_TOOL = 2
    APPROVAL_REQUIRED = 3
    POLICY_REJECTION = 4
    VALIDATION_FAILURE = 5
    TOOL_FAILURE = 6
    CONFLICT = 7
    CANCELLED = 8
    INTERNAL_ERROR = 9
