"""Safe Patch Engine and Smali utilities."""

from noir.patches.engine import PatchEngine, PatchError, PatchValidationError
from noir.patches.smali_templates import (
    SmaliTemplateError,
    generate_permission_check_smali,
    generate_permission_request_smali,
    generate_toast_smali,
    generate_url_redirect_smali,
    wrap_smali_method,
)
from noir.patches.smali_utils import sanitize_smali_content, validate_smali
from noir.patches.smali_validator import (
    SmaliBytecodeValidator,
    SmaliDiagnostic,
    SmaliValidationResult,
)

__all__ = [
    "PatchEngine",
    "PatchError",
    "PatchValidationError",
    "SmaliBytecodeValidator",
    "SmaliDiagnostic",
    "SmaliValidationResult",
    "SmaliTemplateError",
    "generate_url_redirect_smali",
    "generate_toast_smali",
    "generate_permission_check_smali",
    "generate_permission_request_smali",
    "wrap_smali_method",
    "sanitize_smali_content",
    "validate_smali",
]
