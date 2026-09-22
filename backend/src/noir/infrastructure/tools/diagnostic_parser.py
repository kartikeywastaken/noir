"""Structured compiler diagnostics parser for AAPT2 and Apktool."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class CompilerDiagnostic:
    """Structured compiler diagnostic parsed from build tool output."""

    file_path: str | None = None
    line_number: int | None = None
    column_number: int | None = None
    column_end: int | None = None
    severity: str = "error"  # "error" | "warning" | "info"
    message: str = ""
    source_tool: str = "unknown"  # "aapt2" | "apktool" | "unknown"
    failure_shape: str = "unknown"
    raw_line: str = ""


# Regex patterns for various diagnostic formats

# 1. Smali bracket format: smali/foo/Bar.smali[42,15] mismatched input...
_SMALI_BRACKET_RE = re.compile(
    r"^(?:(?P<tool_prefix>[WEI]):\s*)?"
    r"(?P<file>[^\s:\[]+\.smali)\[(?P<line>\d+),(?P<col>\d+)\]:?\s*"
    r"(?:(?:error|warning|note):\s*)?"
    r"(?P<message>.+)$",
    re.IGNORECASE,
)

# 2. File:line:col-col_end or File:line:col format:
#    AndroidManifest.xml:14:5-22: AAPT: error: element <activity> is missing...
#    or W: res/values/strings.xml:10:4: error: ...
_FILE_LINE_COL_RE = re.compile(
    r"^(?:(?P<tool_prefix>[WEI]):\s*)?"
    r"(?P<file>[^\s:\[]+\.[a-zA-Z0-9_.-]+):"
    r"(?P<line>\d+):"
    r"(?P<col>\d+)(?:-(?P<col_end>\d+))?:?\s*"
    r"(?:(?P<aapt_prefix>AAPT):\s*)?"
    r"(?:(?P<severity>error|warning|note|info):\s*)?"
    r"(?P<message>.+)$",
    re.IGNORECASE,
)

# 3. File:line format (no column):
#    res/values/strings.xml:10: error: duplicate value for resource...
#    W: /workspace/res/values/strings.xml:10: error: ...
_FILE_LINE_RE = re.compile(
    r"^(?:(?P<tool_prefix>[WEI]):\s*)?"
    r"(?P<file>[^\s:\[]+\.[a-zA-Z0-9_.-]+):"
    r"(?P<line>\d+):?\s*"
    r"(?:(?P<aapt_prefix>AAPT):\s*)?"
    r"(?:(?P<severity>error|warning|note|info):\s*)?"
    r"(?P<message>.+)$",
    re.IGNORECASE,
)

# 4. E: ... (line X) or (line X): ... format:
#    E: Tag <activity> is missing attribute 'name' (line 42)
#    E: AndroidManifest.xml: tag <activity> is missing attribute 'name' (line 42)
_LINE_PAREN_RE = re.compile(
    r"^(?:(?P<tool_prefix>[WEI]):\s*)?"
    r"(?:(?P<file>[^\s:\[]+\.[a-zA-Z0-9_.-]+):\s*)?"
    r"(?P<message>.+?)\s*\(line\s+(?P<line>\d+)\)$",
    re.IGNORECASE,
)

# 5. File error without line number (e.g. invalid file name, host pollution):
#    W: /workspace/res/drawable/._icon.png: error: invalid file name.
_FILE_NO_LINE_RE = re.compile(
    r"^(?:(?P<tool_prefix>[WEI]):\s*)?"
    r"(?P<file>[^\s:\[]+\.[a-zA-Z0-9_.-]+):\s*"
    r"(?:(?P<aapt_prefix>AAPT):\s*)?"
    r"(?:(?P<severity>error|warning|note|info):\s*)?"
    r"(?P<message>.+)$",
    re.IGNORECASE,
)


def classify_failure_shape(message: str, file_path: str | None = None) -> str:
    """Classify a compiler diagnostic into an actionable failure shape.

    Recognized shapes:
    - host_pollution
    - missing_return_descriptor
    - duplicate_attribute
    - missing_resource_attr
    - duplicate_resource
    - undefined_resource
    - invalid_xml_syntax
    - smali_syntax
    - toolchain_framework
    - unknown
    """
    text = f"{file_path or ''} {message}".lower()

    # 1. Host filesystem pollution (AppleDouble, .DS_Store, MalformedInputException)
    if any(
        marker in text
        for marker in (
            ".ds_store",
            "._",
            "appledouble",
            "malformedinputexception",
            "illegal byte sequence",
            "invalid file name '._",
            "invalid file name '.ds_store'",
        )
    ):
        return "host_pollution"

    # 2. Smali missing return descriptor
    if any(
        marker in text
        for marker in (
            "missing return descriptor",
            "missing return type",
            "no return type",
            "missing return",
        )
    ):
        return "missing_return_descriptor"

    # 3. Duplicate attribute
    if "duplicate attribute" in text or "attribute" in text and "already defined" in text:
        return "duplicate_attribute"

    # 4. Missing resource attribute
    if any(
        marker in text
        for marker in (
            "missing attribute",
            'missing "android:name" attribute',
            "missing 'android:name' attribute",
            "is missing",
            "attribute not found",
            "missing required attribute",
        )
    ):
        return "missing_resource_attr"

    # 5. Duplicate resource entry
    if any(
        marker in text
        for marker in (
            "duplicate value for resource",
            "duplicate resource",
            "resource already defined",
        )
    ):
        return "duplicate_resource"

    # 6. Undefined or unresolved resource reference
    if any(
        marker in text
        for marker in (
            "not found",
            "unresolved reference",
            "no resource found",
            "cannot find symbol",
        )
    ):
        return "undefined_resource"

    # 7. Invalid XML syntax
    if any(
        marker in text
        for marker in (
            "unexpected element",
            "not well-formed",
            "mismatched tag",
            "xml or text declaration not at start",
            "unclosed token",
            "syntax error",
        )
    ) or (file_path and file_path.endswith(".xml") and "error" in text):
        return "invalid_xml_syntax"

    # 8. Smali syntax / assembler errors
    if (file_path and file_path.endswith(".smali")) or any(
        marker in text
        for marker in (
            "mismatched input",
            "expecting end_method",
            "invalid instruction",
            "register overflow",
            "bad register",
            "cannot find method",
        )
    ):
        return "smali_syntax"

    # 9. Toolchain and framework issues
    if any(
        marker in text
        for marker in (
            "android.jar",
            "framework",
            "brutexception",
            "apktool if",
        )
    ):
        return "toolchain_framework"

    return "unknown"


def _determine_source_tool(
    line: str,
    tool_prefix: str | None,
    aapt_prefix: str | None,
    default_tool: str,
) -> str:
    if aapt_prefix or "aapt" in line.lower():
        return "aapt2"
    if tool_prefix in ("W", "I", "E") or "brut" in line or "apktool" in line.lower():
        return "apktool"
    if default_tool in ("aapt2", "apktool"):
        return default_tool
    return "unknown"


def _determine_severity(
    raw_sev: str | None,
    tool_prefix: str | None,
    line: str,
) -> str:
    if raw_sev:
        sev = raw_sev.lower()
        if sev in ("error", "warning", "info"):
            return sev
        if sev == "note":
            return "info"
    if tool_prefix == "E":
        return "error"
    if tool_prefix == "W":
        # Check if line explicitly contains "error:"
        if re.search(r"\berror:\b", line, re.IGNORECASE):
            return "error"
        return "warning"
    if tool_prefix == "I":
        return "info"
    if re.search(r"\b(?:error|exception|failed)\b", line, re.IGNORECASE):
        return "error"
    if re.search(r"\bwarning\b", line, re.IGNORECASE):
        return "warning"
    return "error"


def parse_single_diagnostic(
    line: str,
    default_tool: str = "unknown",
) -> CompilerDiagnostic | None:
    """Parse a single line of compiler output into a CompilerDiagnostic."""
    raw = line.strip()
    if not raw:
        return None

    # Check for Java NIO / Host pollution without standard prefix
    if "malformedinputexception" in raw.lower() or "illegal byte sequence" in raw.lower():
        return CompilerDiagnostic(
            file_path=None,
            line_number=None,
            column_number=None,
            severity="error",
            message=raw,
            source_tool=default_tool if default_tool != "unknown" else "apktool",
            failure_shape="host_pollution",
            raw_line=raw,
        )

    # 1. Smali bracket syntax: smali/com/foo.smali[42,15] message
    m = _SMALI_BRACKET_RE.match(raw)
    if m:
        d = m.groupdict()
        source_tool = _determine_source_tool(raw, d.get("tool_prefix"), None, "apktool")
        sev = _determine_severity(None, d.get("tool_prefix"), raw)
        msg = d["message"].strip()
        shape = classify_failure_shape(msg, d["file"])
        return CompilerDiagnostic(
            file_path=d["file"],
            line_number=int(d["line"]),
            column_number=int(d["col"]),
            severity=sev,
            message=msg,
            source_tool=source_tool,
            failure_shape=shape,
            raw_line=raw,
        )

    # 2. File:line:col[-col_end] syntax
    m = _FILE_LINE_COL_RE.match(raw)
    if m:
        d = m.groupdict()
        source_tool = _determine_source_tool(
            raw, d.get("tool_prefix"), d.get("aapt_prefix"), default_tool
        )
        sev = _determine_severity(d.get("severity"), d.get("tool_prefix"), raw)
        msg = d["message"].strip()
        col_end = int(d["col_end"]) if d.get("col_end") else None
        shape = classify_failure_shape(msg, d["file"])
        return CompilerDiagnostic(
            file_path=d["file"],
            line_number=int(d["line"]),
            column_number=int(d["col"]),
            column_end=col_end,
            severity=sev,
            message=msg,
            source_tool=source_tool,
            failure_shape=shape,
            raw_line=raw,
        )

    # 3. File:line syntax (no column)
    m = _FILE_LINE_RE.match(raw)
    if m:
        d = m.groupdict()
        source_tool = _determine_source_tool(
            raw, d.get("tool_prefix"), d.get("aapt_prefix"), default_tool
        )
        sev = _determine_severity(d.get("severity"), d.get("tool_prefix"), raw)
        msg = d["message"].strip()
        shape = classify_failure_shape(msg, d["file"])
        return CompilerDiagnostic(
            file_path=d["file"],
            line_number=int(d["line"]),
            column_number=None,
            severity=sev,
            message=msg,
            source_tool=source_tool,
            failure_shape=shape,
            raw_line=raw,
        )

    # 4. E: ... (line X) syntax
    m = _LINE_PAREN_RE.match(raw)
    if m:
        d = m.groupdict()
        source_tool = _determine_source_tool(raw, d.get("tool_prefix"), None, default_tool)
        sev = _determine_severity(None, d.get("tool_prefix"), raw)
        msg = d["message"].strip()
        file_path = d.get("file")
        shape = classify_failure_shape(msg, file_path)
        return CompilerDiagnostic(
            file_path=file_path,
            line_number=int(d["line"]),
            column_number=None,
            severity=sev,
            message=msg,
            source_tool=source_tool,
            failure_shape=shape,
            raw_line=raw,
        )

    # 5. File error without line number
    m = _FILE_NO_LINE_RE.match(raw)
    if m:
        d = m.groupdict()
        file_path = d["file"]
        # Ensure it looks like a real filename, not just arbitrary text with a colon
        if "." in file_path or "/" in file_path or "\\" in file_path:
            source_tool = _determine_source_tool(
                raw, d.get("tool_prefix"), d.get("aapt_prefix"), default_tool
            )
            sev = _determine_severity(d.get("severity"), d.get("tool_prefix"), raw)
            msg = d["message"].strip()
            shape = classify_failure_shape(msg, file_path)
            return CompilerDiagnostic(
                file_path=file_path,
                line_number=None,
                column_number=None,
                severity=sev,
                message=msg,
                source_tool=source_tool,
                failure_shape=shape,
                raw_line=raw,
            )

    # Check for general AAPT/Apktool error line without file:
    if raw.startswith("AAPT: error:") or raw.startswith("error:"):
        clean_msg = re.sub(r"^(?:AAPT:\s*)?error:\s*", "", raw).strip()
        return CompilerDiagnostic(
            file_path=None,
            line_number=None,
            column_number=None,
            severity="error",
            message=clean_msg,
            source_tool="aapt2" if "AAPT" in raw else default_tool,
            failure_shape=classify_failure_shape(clean_msg),
            raw_line=raw,
        )

    return None


def parse_diagnostics(
    output: str,
    default_tool: str = "unknown",
) -> list[CompilerDiagnostic]:
    """Parse multi-line compiler output into a list of CompilerDiagnostics."""
    if not output:
        return []

    diagnostics: list[CompilerDiagnostic] = []
    for line in output.splitlines():
        diag = parse_single_diagnostic(line, default_tool=default_tool)
        if diag is not None:
            diagnostics.append(diag)

    return diagnostics


def parse_build_failure(
    output: str,
    stage: str = "apktool_build",
    default_tool: str = "apktool",
) -> dict[str, Any]:
    """Extract structured failure details compatible with legacy and modern consumers."""
    diagnostics = parse_diagnostics(output, default_tool=default_tool)
    primary = next((d for d in diagnostics if d.severity == "error"), None)
    if primary is None and diagnostics:
        primary = diagnostics[0]

    if primary:
        result: dict[str, Any] = {
            "stage": stage,
            "failure_shape": primary.failure_shape,
            "file": primary.file_path,
            "line": primary.line_number,
            "diagnostic": primary.message,
            "severity": primary.severity,
            "source_tool": primary.source_tool,
        }
        if primary.column_number is not None:
            result["column"] = primary.column_number
        if primary.column_end is not None:
            result["column_end"] = primary.column_end
        return result

    # Fallback to last non-empty line
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    last_line = lines[-1] if lines else ""
    return {
        "stage": stage,
        "failure_shape": classify_failure_shape(last_line),
        "diagnostic": last_line[:1000],
        "source_tool": default_tool,
    }
