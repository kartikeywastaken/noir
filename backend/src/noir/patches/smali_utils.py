"""Utilities and sanitizers for Dalvik Smali bytecode."""

from __future__ import annotations

import re

# Regex matching invoke-* instructions where a method target lacks a return descriptor
# Example: invoke-virtual {v0}, Landroid/widget/Toast;->show()
# Dalvik return descriptors start with [VZBSCIJFD] or L or [
_INVOKE_MISSING_RETURN_RE = re.compile(
    r"(invoke-[^\n]+->[a-zA-Z0-9_\$<>]+(?:\([^\)\n]*\)))(?=[ \t]*(?:\r?\n|$|#))"
)

# Known common void API calls that LLMs frequently emit without 'V'
_KNOWN_VOID_CALLS_RE = re.compile(
    r"(Landroid/widget/Toast;->(?:show|cancel)\(\))(?!\w)"
)


def sanitize_smali_content(smali_text: str) -> str:
    """Sanitize Dalvik Smali instructions against common LLM omissions.

    Dalvik bytecode requires every invoke instruction method descriptor to include
    a return type descriptor (e.g. 'V' for void). Small LLMs (e.g. flash-lite)
    frequently omit the trailing 'V' on void calls like Toast.show(), breaking
    smali assembly during APKTool rebuild.
    """
    if not smali_text:
        return smali_text

    # 1. Known void calls
    smali_text = _KNOWN_VOID_CALLS_RE.sub(r"\g<1>V", smali_text)

    # 2. General invoke-* calls with missing return descriptor
    smali_text = _INVOKE_MISSING_RETURN_RE.sub(r"\g<1>V", smali_text)

    return smali_text


# Pre-flight validator re-exports
from noir.patches.smali_validator import (  # noqa: E402
    SmaliBytecodeValidator,
    SmaliDiagnostic,
    SmaliValidationResult,
)

validate_smali = SmaliBytecodeValidator.validate
