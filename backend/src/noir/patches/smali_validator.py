"""Dalvik Smali Bytecode Pre-flight Validator.

Verifies AI-generated Smali patch operations against Dalvik bytecode syntax
and assembly rules prior to patch acceptance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SmaliDiagnostic:
    """Structured line-level diagnostic for Dalvik Smali syntax errors."""

    line_number: int
    line_content: str
    message: str
    error_code: str
    column: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_number": self.line_number,
            "line_content": self.line_content,
            "message": self.message,
            "error_code": self.error_code,
            "column": self.column,
        }


@dataclass
class SmaliValidationResult:
    """Result of Smali pre-flight validation."""

    is_valid: bool
    diagnostics: list[SmaliDiagnostic] = field(default_factory=list)

    @property
    def error_summary(self) -> str:
        if not self.diagnostics:
            return ""
        return "\n".join(
            f"Line {d.line_number}: [{d.error_code}] {d.message} (Offending line: '{d.line_content}')"
            for d in self.diagnostics
        )


# Known Dalvik opcodes and families
_DALVIK_ZERO_ARG_OPCODES = {"return-void", "nop"}
_DALVIK_ONE_REG_OPCODES = {
    "return",
    "return-object",
    "return-wide",
    "move-result",
    "move-result-object",
    "move-result-wide",
    "move-exception",
    "throw",
    "monitor-enter",
    "monitor-exit",
}
_DALVIK_FIELD_OPCODES = {
    "sget", "sget-wide", "sget-object", "sget-boolean", "sget-byte", "sget-char", "sget-short",
    "sput", "sput-wide", "sput-object", "sput-boolean", "sput-byte", "sput-char", "sput-short",
    "iget", "iget-wide", "iget-object", "iget-boolean", "iget-byte", "iget-char", "iget-short",
    "iput", "iput-wide", "iput-object", "iput-boolean", "iput-byte", "iput-char", "iput-short",
}
_DALVIK_TWO_REG_OPCODES = {
    "move", "move/from16", "move/16",
    "move-wide", "move-wide/from16", "move-wide/16",
    "move-object", "move-object/from16", "move-object/16",
    "array-length",
    "neg-int", "not-int", "neg-long", "not-long", "neg-float", "neg-double",
    "int-to-long", "int-to-float", "int-to-double",
    "long-to-int", "long-to-float", "long-to-double",
    "float-to-int", "float-to-long", "float-to-double",
    "double-to-int", "double-to-long", "double-to-float",
    "int-to-byte", "int-to-char", "int-to-short",
    "add-int/2addr", "sub-int/2addr", "mul-int/2addr", "div-int/2addr", "rem-int/2addr",
    "and-int/2addr", "or-int/2addr", "xor-int/2addr", "shl-int/2addr", "shr-int/2addr", "ushr-int/2addr",
    "add-long/2addr", "sub-long/2addr", "mul-long/2addr", "div-long/2addr", "rem-long/2addr",
    "and-long/2addr", "or-long/2addr", "xor-long/2addr", "shl-long/2addr", "shr-long/2addr", "ushr-long/2addr",
    "add-float/2addr", "sub-float/2addr", "mul-float/2addr", "div-float/2addr", "rem-float/2addr",
    "add-double/2addr", "sub-double/2addr", "mul-double/2addr", "div-double/2addr", "rem-double/2addr",
}
_DALVIK_THREE_REG_OPCODES = {
    "add-int", "sub-int", "mul-int", "div-int", "rem-int", "and-int", "or-int", "xor-int", "shl-int", "shr-int", "ushr-int",
    "add-float", "sub-float", "mul-float", "div-float", "rem-float",
    "cmpl-float", "cmpg-float",
    "add-long", "sub-long", "mul-long", "div-long", "rem-long", "and-long", "or-long", "xor-long",
    "shl-long", "shr-long", "ushr-long",
    "add-double", "sub-double", "mul-double", "div-double", "rem-double",
    "cmpl-double", "cmpg-double", "cmp-long",
    "aget", "aget-wide", "aget-object", "aget-boolean", "aget-byte", "aget-char", "aget-short",
    "aput", "aput-wide", "aput-object", "aput-boolean", "aput-byte", "aput-char", "aput-short",
}
_WIDE_OPCODES = {
    "const-wide", "const-wide/16", "const-wide/32", "const-wide/high16",
    "move-wide", "move-wide/from16", "move-wide/16",
    "return-wide",
    "move-result-wide",
    "sget-wide", "sput-wide", "iget-wide", "iput-wide",
    "aget-wide", "aput-wide",
    "cmpl-double", "cmpg-double", "cmp-long",
    "add-long", "sub-long", "mul-long", "div-long", "rem-long",
    "and-long", "or-long", "xor-long", "shl-long", "shr-long", "ushr-long",
    "add-double", "sub-double", "mul-double", "div-double", "rem-double",
    "neg-long", "not-long", "neg-double",
    "add-long/2addr", "sub-long/2addr", "mul-long/2addr", "div-long/2addr", "rem-long/2addr",
    "and-long/2addr", "or-long/2addr", "xor-long/2addr", "shl-long/2addr", "shr-long/2addr", "ushr-long/2addr",
    "add-double/2addr", "sub-double/2addr", "mul-double/2addr", "div-double/2addr", "rem-double/2addr",
}
_DALVIK_INVOKE_PREFIXES = (
    "invoke-virtual",
    "invoke-super",
    "invoke-direct",
    "invoke-static",
    "invoke-interface",
    "invoke-custom",
    "invoke-polymorphic",
)
_NUMERIC_CONST_OPCODES = {
    "const/4", "const/16", "const", "const/high16",
    "const-wide/16", "const-wide/32", "const-wide", "const-wide/high16",
}
_LITERAL_ARITH_OPCODES = {
    "add-int/lit8", "rsub-int/lit8", "mul-int/lit8", "div-int/lit8", "rem-int/lit8",
    "and-int/lit8", "or-int/lit8", "xor-int/lit8", "shl-int/lit8", "shr-int/lit8", "ushr-int/lit8",
    "add-int/lit16", "rsub-int", "mul-int/lit16", "div-int/lit16", "rem-int/lit16",
    "and-int/lit16", "or-int/lit16", "xor-int/lit16",
}
_BRANCH_OPCODES = {
    "goto", "goto/16", "goto/32",
    "if-eq", "if-ne", "if-lt", "if-ge", "if-gt", "if-le",
    "if-eqz", "if-nez", "if-ltz", "if-gez", "if-gtz", "if-lez",
}
_DATA_PAYLOAD_OPCODES = {
    "packed-switch", "sparse-switch", "fill-array-data",
}
_TYPE_OPCODES = {
    "new-instance", "check-cast", "instance-of", "new-array",
    "filled-new-array", "filled-new-array/range",
}
_ALL_VALID_DALVIK_OPCODES = (
    _DALVIK_ZERO_ARG_OPCODES
    | _DALVIK_ONE_REG_OPCODES
    | _DALVIK_TWO_REG_OPCODES
    | _DALVIK_THREE_REG_OPCODES
    | _DALVIK_FIELD_OPCODES
    | _NUMERIC_CONST_OPCODES
    | {"const-string", "const-string/jumbo", "const-class", "const-method-handle", "const-method-type"}
    | _BRANCH_OPCODES
    | _DATA_PAYLOAD_OPCODES
    | _TYPE_OPCODES
    | _LITERAL_ARITH_OPCODES
    | {
        "invoke-virtual", "invoke-super", "invoke-direct", "invoke-static", "invoke-interface",
        "invoke-virtual/range", "invoke-super/range", "invoke-direct/range", "invoke-static/range", "invoke-interface/range",
        "invoke-polymorphic", "invoke-polymorphic/range",
        "invoke-custom", "invoke-custom/range",
    }
)
_KNOWN_DIRECTIVES = {
    ".class", ".super", ".implements", ".source",
    ".field", ".end field",
    ".method", ".end method",
    ".registers", ".locals",
    ".parameter", ".param", ".end param",
    ".prologue", ".line",
    ".local", ".end local", ".restart local",
    ".annotation", ".end annotation",
    ".subannotation", ".end subannotation",
    ".catch", ".catchall",
    ".packed-switch", ".end packed-switch",
    ".sparse-switch", ".end sparse-switch",
    ".array-data", ".end array-data",
    ".enum",
}

_PRIMITIVE_TYPES = set("ZBCSIJFD")
_METHOD_SIG_RE = re.compile(
    r"^([a-zA-Z0-9_\$<>]+)\(([^)]*)\)(.*)$"
)


def _split_params(param_str: str) -> list[str]:
    """Split Dalvik parameter descriptors without requiring delimiters."""
    params: list[str] = []
    i = 0
    while i < len(param_str):
        c = param_str[i]
        if c in _PRIMITIVE_TYPES:
            params.append(c)
            i += 1
        elif c == "[":
            array_depth = 0
            while i < len(param_str) and param_str[i] == "[":
                array_depth += 1
                i += 1
            if i < len(param_str) and param_str[i] in _PRIMITIVE_TYPES:
                params.append("[" * array_depth + param_str[i])
                i += 1
            elif i < len(param_str) and param_str[i] == "L":
                semi = param_str.find(";", i)
                if semi != -1:
                    params.append("[" * array_depth + param_str[i : semi + 1])
                    i = semi + 1
                else:
                    params.append(param_str[i:])
                    break
            else:
                params.append("[" * array_depth)
        elif c == "L":
            semi = param_str.find(";", i)
            if semi != -1:
                params.append(param_str[i : semi + 1])
                i = semi + 1
            else:
                params.append(param_str[i:])
                break
        else:
            params.append(c)
            i += 1
    return params


def _param_register_count(param: str) -> int:
    """Return number of Dalvik registers required for a parameter descriptor."""
    if param in ("J", "D"):
        return 2
    return 1


def _is_valid_type_descriptor(desc: str, allow_void: bool = True) -> bool:
    """Verify if desc is a valid Dalvik type descriptor."""
    desc = desc.strip()
    if not desc:
        return False
    if desc == "V":
        return allow_void
    if desc in _PRIMITIVE_TYPES:
        return True
    if desc.startswith("["):
        # Array element can never be void in Dalvik/Java
        return _is_valid_type_descriptor(desc[1:], allow_void=False)
    if desc.startswith("L") and desc.endswith(";") and len(desc) > 2:
        inner = desc[1:-1]
        return not inner.endswith("/") and not inner.startswith("/") and ";" not in inner
    return False


def _get_param_reg_count(sig: str, static_flag: bool) -> int | None:
    """Compute number of parameter registers expected for a method signature."""
    if not sig:
        return None
    m = _METHOD_SIG_RE.search(sig)
    if not m:
        return None
    _, p_str, _ = m.groups()
    params = _split_params(p_str)
    return (0 if static_flag else 1) + sum(_param_register_count(p) for p in params)


def _is_valid_quoted_string(s: str) -> bool:
    """Verify that s is an unescaped closed quoted string literal."""
    s = s.strip()
    if len(s) < 2 or not s.startswith('"') or not s.endswith('"'):
        return False
    # Count consecutive backslashes immediately preceding the final quote
    i = len(s) - 2
    slash_count = 0
    while i >= 0 and s[i] == "\\":
        slash_count += 1
        i -= 1
    return (slash_count % 2) == 0


class SmaliBytecodeValidator:
    """Pre-flight validator for Dalvik Smali bytecode."""

    @classmethod
    def validate(
        cls,
        smali_text: str,
        *,
        context_method: str | None = None,
        context_class: str | None = None,
        enclosing_file_content: str | None = None,
    ) -> SmaliValidationResult:
        """Validate Smali source code against Dalvik assembly rules.

        Args:
            smali_text: Smali instructions or full method block to validate.
            context_method: Optional method signature if validating a partial snippet.
            context_class: Optional class descriptor if validating a partial snippet.
            enclosing_file_content: Optional full content of the target file.

        Returns:
            SmaliValidationResult with structured line-level diagnostics.
        """
        if not smali_text or not smali_text.strip():
            return SmaliValidationResult(is_valid=True)

        diagnostics: list[SmaliDiagnostic] = []
        lines = smali_text.splitlines()

        # Track method context
        in_method = False
        method_sig = context_method or ""
        is_static = False
        declared_registers: int | None = None
        declared_locals: int | None = None
        labels_defined: set[str] = set()
        labels_referenced: list[tuple[str, int, str]] = []  # (label, line_num, line_str)
        last_invoke: tuple[str, str, int] | None = None  # (method_name, return_type, line_idx)
        in_packed_switch = False
        in_sparse_switch = False
        in_array_data = False
        annotation_depth = 0

        # If snippet inside an existing method from enclosing_file_content:
        if enclosing_file_content and context_method and not in_method:
            out_ctx: dict[str, Any] = {
                "locals": None,
                "registers": None,
                "static": False,
                "labels": set(),
            }
            cls._extract_enclosing_context(
                enclosing_file_content,
                context_method,
                out_state=out_ctx,
            )
            if out_ctx.get("locals") is not None:
                declared_locals = out_ctx["locals"]
            if out_ctx.get("registers") is not None:
                declared_registers = out_ctx["registers"]
            is_static = bool(out_ctx.get("static", False))
            labels_defined.update(out_ctx.get("labels", set()))

        param_reg_count = _get_param_reg_count(method_sig, is_static)

        for line_idx, line in enumerate(lines, start=1):
            raw_line = line
            # Strip comments, but preserve line content for reporting
            code_line = line.split("#", 1)[0].strip()
            if not code_line:
                continue

            # Method boundary directives
            if code_line.startswith(".method"):
                if in_method:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message="Nested .method declarations are illegal in Smali.",
                            error_code="NESTED_METHOD",
                        )
                    )
                in_method = True
                is_static = " static " in f" {code_line} "
                # Extract signature
                parts = code_line.split()
                if parts:
                    method_sig = parts[-1]
                param_reg_count = _get_param_reg_count(method_sig, is_static)
                last_invoke = None
                continue

            if code_line.startswith(".end method"):
                if not in_method and not context_method:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message="Unmatched .end method without preceding .method.",
                            error_code="UNMATCHED_END_METHOD",
                        )
                    )
                if annotation_depth > 0:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=f"Unclosed .annotation / .subannotation block inside method (depth {annotation_depth}).",
                            error_code="UNCLOSED_ANNOTATION",
                        )
                    )
                    annotation_depth = 0
                if in_packed_switch:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message="Unclosed .packed-switch block before .end method.",
                            error_code="UNCLOSED_DATA_PAYLOAD",
                        )
                    )
                if in_sparse_switch:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message="Unclosed .sparse-switch block before .end method.",
                            error_code="UNCLOSED_DATA_PAYLOAD",
                        )
                    )
                if in_array_data:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message="Unclosed .array-data block before .end method.",
                            error_code="UNCLOSED_DATA_PAYLOAD",
                        )
                    )
                # Check label references for the method
                for lbl, lbl_line_num, lbl_line_content in labels_referenced:
                    if lbl not in labels_defined:
                        diagnostics.append(
                            SmaliDiagnostic(
                                line_number=lbl_line_num,
                                line_content=lbl_line_content,
                                message=f"Branch target label '{lbl}' is undefined.",
                                error_code="UNDEFINED_LABEL",
                            )
                        )
                in_method = False
                declared_registers = None
                declared_locals = None
                labels_defined = set()
                labels_referenced = []
                last_invoke = None
                in_packed_switch = False
                in_sparse_switch = False
                in_array_data = False
                annotation_depth = 0
                continue

            # Directives for data payloads and annotations
            if code_line.startswith(".annotation") or re.search(r"(?:^|\s)\.subannotation\b", code_line):
                annotation_depth += 1
                continue
            if code_line.startswith((".end annotation", ".end subannotation")):
                if annotation_depth == 0:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message="Unmatched .end annotation / .end subannotation directive.",
                            error_code="UNMATCHED_END_ANNOTATION",
                        )
                    )
                else:
                    annotation_depth -= 1
                continue
            if annotation_depth > 0:
                continue

            if code_line.startswith(".array-data"):
                in_array_data = True
                continue
            if code_line.startswith(".end array-data"):
                if not in_array_data:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message="Unmatched .end array-data directive without preceding .array-data.",
                            error_code="UNMATCHED_END_DIRECTIVE",
                        )
                    )
                in_array_data = False
                continue
            if in_array_data:
                continue

            if code_line.startswith(".packed-switch"):
                in_packed_switch = True
                continue
            if code_line.startswith(".end packed-switch"):
                if not in_packed_switch:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message="Unmatched .end packed-switch directive without preceding .packed-switch.",
                            error_code="UNMATCHED_END_DIRECTIVE",
                        )
                    )
                in_packed_switch = False
                continue
            if in_packed_switch:
                if code_line.startswith(":"):
                    target_lbl = code_line.split()[0]
                    labels_referenced.append((target_lbl, line_idx, raw_line))
                continue

            if code_line.startswith(".sparse-switch"):
                in_sparse_switch = True
                continue
            if code_line.startswith(".end sparse-switch"):
                if not in_sparse_switch:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message="Unmatched .end sparse-switch directive without preceding .sparse-switch.",
                            error_code="UNMATCHED_END_DIRECTIVE",
                        )
                    )
                in_sparse_switch = False
                continue
            if in_sparse_switch:
                if "->" in code_line:
                    target_lbl = code_line.split("->", 1)[1].strip()
                    if target_lbl.startswith(":"):
                        labels_referenced.append((target_lbl.split()[0], line_idx, raw_line))
                continue

            # Exception handling directives
            if code_line.startswith(".catch ") or code_line.startswith(".catchall "):
                brace_match = re.search(r"\{([^}]+)\}", code_line)
                if brace_match:
                    range_str = brace_match.group(1).strip()
                    if ".." in range_str:
                        t_start, t_end = [p.strip() for p in range_str.split("..", 1)]
                        if t_start.startswith(":"):
                            labels_referenced.append((t_start, line_idx, raw_line))
                        if t_end.startswith(":"):
                            labels_referenced.append((t_end, line_idx, raw_line))
                if "}" in code_line:
                    after_brace = code_line.split("}", 1)[1].strip()
                    if after_brace.startswith(":"):
                        labels_referenced.append((after_brace.split()[0], line_idx, raw_line))
                if code_line.startswith(".catch "):
                    type_part = code_line[len(".catch "):code_line.find("{")].strip()
                    if type_part and not _is_valid_type_descriptor(type_part, allow_void=False):
                        diagnostics.append(
                            SmaliDiagnostic(
                                line_number=line_idx,
                                line_content=raw_line,
                                message=f"Invalid exception type descriptor '{type_part}' in .catch directive.",
                                error_code="INVALID_TYPE_DESCRIPTOR",
                            )
                        )
                continue

            # Check for label definition
            if code_line.startswith(":"):
                label_name = code_line.split()[0]
                if label_name in labels_defined:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=(
                                f"Label '{label_name}' is defined more than once in the "
                                "same method."
                            ),
                            error_code="DUPLICATE_LABEL",
                        )
                    )
                labels_defined.add(label_name)
                continue

            # Check .registers / .locals directives
            if code_line.startswith(".registers"):
                parts = code_line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    declared_registers = int(parts[1])
                    if param_reg_count is not None and declared_registers < param_reg_count:
                        diagnostics.append(
                            SmaliDiagnostic(
                                line_number=line_idx,
                                line_content=raw_line,
                                message=(
                                    f"Declared .registers count ({declared_registers}) is less than the number of "
                                    f"parameter registers required by the method signature ({param_reg_count})."
                                ),
                                error_code="INSUFFICIENT_REGISTERS",
                            )
                        )
                else:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message="Malformed .registers directive. Expected integer count.",
                            error_code="MALFORMED_DIRECTIVE",
                        )
                    )
                continue

            if code_line.startswith(".locals"):
                parts = code_line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    declared_locals = int(parts[1])
                else:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message="Malformed .locals directive. Expected integer count.",
                            error_code="MALFORMED_DIRECTIVE",
                        )
                    )
                continue

            # Skip or check other valid directives
            if code_line.startswith("."):
                dir_name = " ".join(code_line.split()[:2]) if code_line.startswith(".end ") else code_line.split()[0]
                if dir_name not in _KNOWN_DIRECTIVES:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=f"Unknown or unrecognized Smali directive '{dir_name}'.",
                            error_code="UNKNOWN_DIRECTIVE",
                        )
                    )
                continue

            # Now validate instruction
            last_invoke = cls._validate_instruction(
                code_line=code_line,
                raw_line=raw_line,
                line_idx=line_idx,
                diagnostics=diagnostics,
                labels_referenced=labels_referenced,
                declared_registers=declared_registers,
                declared_locals=declared_locals,
                is_static=is_static,
                method_sig=method_sig,
                param_reg_count=param_reg_count,
                last_invoke=last_invoke,
            )

        # End of input checks
        if in_method and not context_method:
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=len(lines),
                    line_content=lines[-1] if lines else "",
                    message="Missing .end method before end of file.",
                    error_code="UNCLOSED_METHOD",
                )
            )

        if annotation_depth > 0:
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=len(lines),
                    line_content=lines[-1] if lines else "",
                    message=f"Unclosed .annotation / .subannotation block at end of input (depth {annotation_depth}).",
                    error_code="UNCLOSED_ANNOTATION",
                )
            )

        if in_packed_switch:
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=len(lines),
                    line_content=lines[-1] if lines else "",
                    message="Unclosed .packed-switch block at end of input.",
                    error_code="UNCLOSED_DATA_PAYLOAD",
                )
            )
        if in_sparse_switch:
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=len(lines),
                    line_content=lines[-1] if lines else "",
                    message="Unclosed .sparse-switch block at end of input.",
                    error_code="UNCLOSED_DATA_PAYLOAD",
                )
            )
        if in_array_data:
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=len(lines),
                    line_content=lines[-1] if lines else "",
                    message="Unclosed .array-data block at end of input.",
                    error_code="UNCLOSED_DATA_PAYLOAD",
                )
            )

        # Check label references if any remain
        for lbl, lbl_line_num, lbl_line_content in labels_referenced:
            if lbl not in labels_defined:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=lbl_line_num,
                        line_content=lbl_line_content,
                        message=f"Branch target label '{lbl}' is undefined.",
                        error_code="UNDEFINED_LABEL",
                    )
                )

        return SmaliValidationResult(
            is_valid=len(diagnostics) == 0,
            diagnostics=diagnostics,
        )

    @classmethod
    def _validate_instruction(
        cls,
        code_line: str,
        raw_line: str,
        line_idx: int,
        diagnostics: list[SmaliDiagnostic],
        labels_referenced: list[tuple[str, int, str]],
        declared_registers: int | None,
        declared_locals: int | None,
        is_static: bool,
        method_sig: str,
        param_reg_count: int | None,
        last_invoke: tuple[str, str, int] | None,
    ) -> tuple[str, str, int] | None:
        """Validate an individual Smali instruction line.

        Returns updated last_invoke state for move-result pairing.
        """
        # Check invoke instructions
        is_invoke = any(code_line.startswith(prefix) for prefix in _DALVIK_INVOKE_PREFIXES)
        if is_invoke:
            return cls._validate_invoke_instruction(
                code_line=code_line,
                raw_line=raw_line,
                line_idx=line_idx,
                diagnostics=diagnostics,
                declared_registers=declared_registers,
                declared_locals=declared_locals,
                is_static=is_static,
                method_sig=method_sig,
                param_reg_count=param_reg_count,
            )

        # Check filled-new-array instructions
        if code_line.startswith("filled-new-array"):
            arr_type = cls._validate_filled_new_array(
                code_line=code_line,
                raw_line=raw_line,
                line_idx=line_idx,
                diagnostics=diagnostics,
                declared_registers=declared_registers,
                declared_locals=declared_locals,
                param_reg_count=param_reg_count,
            )
            return ("filled-new-array", arr_type or "[Ljava/lang/Object;", line_idx)

        # Parse opcode and operands
        parts = code_line.split(None, 1)
        opcode = parts[0]
        operands = parts[1].strip() if len(parts) > 1 else ""

        # Check zero-arg opcodes
        if opcode in _DALVIK_ZERO_ARG_OPCODES:
            if operands:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Opcode '{opcode}' does not take operands; found '{operands}'.",
                        error_code="ILLEGAL_OPERAND",
                    )
                )
            if opcode == "return-void" and method_sig:
                cls._check_return_type_match(
                    opcode, line_idx, raw_line, diagnostics, method_sig
                )
            return None

        # Check move-result instructions
        if opcode.startswith("move-result"):
            cls._validate_move_result(
                opcode=opcode,
                operands=operands,
                line_idx=line_idx,
                raw_line=raw_line,
                diagnostics=diagnostics,
                declared_registers=declared_registers,
                declared_locals=declared_locals,
                param_reg_count=param_reg_count,
                last_invoke=last_invoke,
            )
            return None

        # Check return instructions (return, return-object, return-wide)
        if opcode.startswith("return"):
            if not operands or "," in operands:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Opcode '{opcode}' requires exactly one register operand.",
                        error_code="ILLEGAL_OPERAND",
                    )
                )
            else:
                cls._check_register(
                    operands.strip(),
                    line_idx,
                    raw_line,
                    diagnostics,
                    declared_registers,
                    declared_locals,
                    param_reg_count,
                    is_wide=(opcode == "return-wide"),
                )
                cls._check_return_type_match(
                    opcode, line_idx, raw_line, diagnostics, method_sig
                )
            return None

        # Check other single register opcodes (move-exception, throw)
        if opcode in _DALVIK_ONE_REG_OPCODES:
            if not operands or "," in operands:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Opcode '{opcode}' requires exactly one register operand.",
                        error_code="ILLEGAL_OPERAND",
                    )
                )
            else:
                cls._check_register(
                    operands.strip(),
                    line_idx,
                    raw_line,
                    diagnostics,
                    declared_registers,
                    declared_locals,
                    param_reg_count,
                )
            return None

        # Check branch opcodes
        if opcode in {"goto", "goto/16", "goto/32"}:
            if not operands.startswith(":"):
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Opcode '{opcode}' requires a target label starting with ':'.",
                        error_code="ILLEGAL_OPERAND",
                    )
                )
            else:
                labels_referenced.append((operands.strip(), line_idx, raw_line))
            return None

        # Check if-*z opcodes
        if re.match(r"^if-[a-z]+z$", opcode):
            ops = [op.strip() for op in operands.split(",")]
            if len(ops) != 2 or not ops[1].startswith(":"):
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Opcode '{opcode}' requires one register and one target label (e.g. {opcode} v0, :label).",
                        error_code="ILLEGAL_OPERAND",
                    )
                )
            else:
                cls._check_register(
                    ops[0], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count
                )
                labels_referenced.append((ops[1], line_idx, raw_line))
            return None

        # Check if-* opcodes (two registers)
        if re.match(r"^if-[a-z]+$", opcode):
            ops = [op.strip() for op in operands.split(",")]
            if len(ops) != 3 or not ops[2].startswith(":"):
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Opcode '{opcode}' requires two registers and one target label (e.g. {opcode} v0, v1, :label).",
                        error_code="ILLEGAL_OPERAND",
                    )
                )
            else:
                cls._check_register(
                    ops[0], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count
                )
                cls._check_register(
                    ops[1], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count
                )
                labels_referenced.append((ops[2], line_idx, raw_line))
            return None

        # Check switch and array-data instructions
        if opcode in {"packed-switch", "sparse-switch", "fill-array-data"}:
            ops = [op.strip() for op in operands.split(",")]
            if len(ops) != 2 or not ops[1].startswith(":"):
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Opcode '{opcode}' requires one register and one target label (e.g. {opcode} v0, :label).",
                        error_code="ILLEGAL_OPERAND",
                    )
                )
            else:
                cls._check_register(
                    ops[0], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count
                )
                labels_referenced.append((ops[1], line_idx, raw_line))
            return None

        # Check const-string
        if opcode in {"const-string", "const-string/jumbo"}:
            ops = [op.strip() for op in operands.split(",", 1)]
            if len(ops) != 2 or not _is_valid_quoted_string(ops[1]):
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Opcode '{opcode}' requires a register and a valid closed quoted string literal (e.g. {opcode} v0, \"text\").",
                        error_code="ILLEGAL_OPERAND",
                    )
                )
            else:
                cls._check_register(
                    ops[0], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count
                )
            return None

        # Check const-class
        if opcode == "const-class":
            ops = [op.strip() for op in operands.split(",", 1)]
            if len(ops) != 2:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message="Opcode 'const-class' requires a register and a type descriptor (e.g. const-class v0, Ljava/lang/String;).",
                        error_code="ILLEGAL_OPERAND",
                    )
                )
            else:
                cls._check_register(ops[0], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count)
                type_desc = ops[1].strip()
                if not _is_valid_type_descriptor(type_desc, allow_void=False):
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=f"Invalid class/type descriptor '{type_desc}' for 'const-class'.",
                            error_code="INVALID_TYPE_DESCRIPTOR",
                        )
                    )
            return None

        # Check const-method-type (Dalvik 038+)
        if opcode == "const-method-type":
            ops = [op.strip() for op in operands.split(",", 1)]
            if len(ops) != 2:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message="Opcode 'const-method-type' requires a register and a method prototype (e.g. const-method-type v0, (I)V).",
                        error_code="ILLEGAL_OPERAND",
                    )
                )
            else:
                cls._check_register(ops[0], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count)
                proto = ops[1].strip()
                m_proto = re.match(r"^\(([^)]*)\)(.+)$", proto)
                if not m_proto:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=f"Malformed method prototype '{proto}' for 'const-method-type'. Expected '(params)ReturnType'.",
                            error_code="MALFORMED_METHOD_SIGNATURE",
                        )
                    )
                else:
                    p_params, p_ret = m_proto.groups()
                    if not _is_valid_type_descriptor(p_ret, allow_void=True):
                        diagnostics.append(
                            SmaliDiagnostic(
                                line_number=line_idx,
                                line_content=raw_line,
                                message=f"Invalid return type descriptor '{p_ret}' in 'const-method-type'.",
                                error_code="INVALID_RETURN_TYPE",
                            )
                        )
                    for pt in _split_params(p_params):
                        if not _is_valid_type_descriptor(pt, allow_void=False):
                            diagnostics.append(
                                SmaliDiagnostic(
                                    line_number=line_idx,
                                    line_content=raw_line,
                                    message=f"Invalid parameter type descriptor '{pt}' in 'const-method-type'.",
                                    error_code="INVALID_PARAM_DESCRIPTOR",
                                )
                            )
            return None

        # Check const-method-handle (Dalvik 038+)
        if opcode == "const-method-handle":
            ops = [op.strip() for op in operands.split(",", 2)]
            if len(ops) != 3:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=(
                            "Opcode 'const-method-handle' requires register, handle type, and target reference "
                            "(e.g. const-method-handle v0, invoke-static, Lclass;->method()V)."
                        ),
                        error_code="ILLEGAL_OPERAND",
                    )
                )
            else:
                cls._check_register(ops[0], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count)
                handle_type = ops[1].strip()
                target_ref = ops[2].strip()
                valid_handles = {
                    "static-put", "static-get", "instance-put", "instance-get",
                    "invoke-static", "invoke-instance", "invoke-constructor", "invoke-direct", "invoke-interface",
                }
                if handle_type not in valid_handles:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=f"Invalid handle type '{handle_type}' for 'const-method-handle'. Expected one of: {sorted(valid_handles)}.",
                            error_code="ILLEGAL_OPERAND",
                        )
                    )
                if "->" not in target_ref:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=f"Malformed target reference '{target_ref}' in 'const-method-handle'. Missing '->'.",
                            error_code="MALFORMED_METHOD_TARGET",
                        )
                    )
            return None

        # Check numeric const instructions
        if opcode in _NUMERIC_CONST_OPCODES:
            ops = [op.strip() for op in operands.split(",", 1)]
            if len(ops) != 2:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Opcode '{opcode}' requires a register and a numeric literal (e.g. {opcode} v0, 0x1).",
                        error_code="ILLEGAL_OPERAND",
                    )
                )
            else:
                is_wide_op = opcode in _WIDE_OPCODES or opcode == "const-wide"
                cls._check_register(
                    ops[0], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count, is_wide=is_wide_op
                )
                lit_str = ops[1].strip()
                lit_to_parse = lit_str.rstrip("Ll") if (is_wide_op or lit_str.endswith(("L", "l"))) else lit_str
                val: int | None = None
                try:
                    if lit_to_parse.startswith(("0x", "-0x", "+0x")):
                        val = int(lit_to_parse, 16)
                    else:
                        val = int(lit_to_parse, 10)
                except ValueError:
                    val = None

                if val is None:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=f"Opcode '{opcode}' requires a valid numeric literal, found '{lit_str}'.",
                            error_code="ILLEGAL_OPERAND",
                        )
                    )
                elif opcode == "const/4":
                    if val < -8 or val > 7:
                        diagnostics.append(
                            SmaliDiagnostic(
                                line_number=line_idx,
                                line_content=raw_line,
                                message=f"Literal value '{lit_str}' ({val}) is out of range for 'const/4' (valid 4-bit signed range: -8 to 7).",
                                error_code="LITERAL_OUT_OF_RANGE",
                            )
                        )
                elif opcode == "const/16":
                    if val < -32768 or val > 32767:
                        diagnostics.append(
                            SmaliDiagnostic(
                                line_number=line_idx,
                                line_content=raw_line,
                                message=f"Literal value '{lit_str}' ({val}) is out of range for 'const/16' (valid 16-bit signed range: -32768 to 32767).",
                                error_code="LITERAL_OUT_OF_RANGE",
                            )
                        )
                elif opcode == "const/high16":
                    if val < -0x80000000 or val > 0x7FFFFFFF:
                        diagnostics.append(
                            SmaliDiagnostic(
                                line_number=line_idx,
                                line_content=raw_line,
                                message=f"Literal value '{lit_str}' ({val}) is out of range for 32-bit integer.",
                                error_code="LITERAL_OUT_OF_RANGE",
                            )
                        )
                    elif (val & 0xFFFF) != 0:
                        diagnostics.append(
                            SmaliDiagnostic(
                                line_number=line_idx,
                                line_content=raw_line,
                                message=f"Literal value '{lit_str}' has non-zero lower 16 bits. 'const/high16' requires lower 16 bits to be 0.",
                                error_code="LITERAL_OUT_OF_RANGE",
                            )
                        )
            return None

        # Check field access opcodes (sget*, sput*, iget*, iput*)
        is_sfield = opcode.startswith("sget") or opcode.startswith("sput")
        is_ifield = opcode.startswith("iget") or opcode.startswith("iput")
        if (is_sfield or is_ifield) and opcode in _DALVIK_FIELD_OPCODES:
            ops = [op.strip() for op in operands.split(",")]
            expected_count = 2 if is_sfield else 3
            if len(ops) != expected_count:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Opcode '{opcode}' expects {expected_count} operand(s) ({'1 register, 1 field' if is_sfield else '2 registers, 1 field'}), but found {len(ops)}.",
                        error_code="ILLEGAL_OPERAND",
                    )
                )
                return None

            is_wide_op = "-wide" in opcode or opcode in _WIDE_OPCODES
            cls._check_register(
                ops[0], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count, is_wide=is_wide_op
            )
            if is_ifield:
                cls._check_register(
                    ops[1], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count, is_wide=False
                )

            field_ref = ops[-1]
            if "->" not in field_ref or ":" not in field_ref:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Malformed field reference '{field_ref}'. Expected 'Lclass/descriptor;->fieldName:TypeDescriptor'.",
                        error_code="MISSING_FIELD_TYPE" if ":" not in field_ref else "MALFORMED_FIELD_TARGET",
                    )
                )
            else:
                class_part, rest = field_ref.split("->", 1)
                if not (class_part.startswith("L") or class_part.startswith("[")):
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=f"Invalid class descriptor '{class_part}' in field reference.",
                            error_code="INVALID_CLASS_DESCRIPTOR",
                        )
                    )
                field_name, field_type = rest.split(":", 1)
                if not field_name.strip():
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=f"Missing field name in field reference '{field_ref}'.",
                            error_code="MALFORMED_FIELD_TARGET",
                        )
                    )
                if not _is_valid_type_descriptor(field_type, allow_void=False):
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=f"Invalid field type descriptor '{field_type}' in field reference.",
                            error_code="INVALID_FIELD_TYPE",
                        )
                    )
            return None

        # Check /lit8 and /lit16 arithmetic instructions (including rsub-int)
        is_lit8 = "/lit8" in opcode
        is_lit16 = "/lit16" in opcode or opcode == "rsub-int"
        if is_lit8 or is_lit16:
            ops = [op.strip() for op in operands.split(",")]
            if len(ops) != 3:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Opcode '{opcode}' requires two registers and one numeric literal (e.g. {opcode} v0, v1, 10).",
                        error_code="ILLEGAL_OPERAND",
                    )
                )
            else:
                cls._check_register(
                    ops[0], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count
                )
                cls._check_register(
                    ops[1], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count
                )
                lit_str = ops[2]
                try:
                    lit_val = int(lit_str, 16) if lit_str.startswith(("0x", "-0x", "+0x")) else int(lit_str, 10)
                    if is_lit8 and (lit_val < -128 or lit_val > 127):
                        diagnostics.append(
                            SmaliDiagnostic(
                                line_number=line_idx,
                                line_content=raw_line,
                                message=f"Literal value '{lit_str}' ({lit_val}) is out of range for signed 8-bit ({opcode}, valid: -128 to 127).",
                                error_code="LITERAL_OUT_OF_RANGE",
                            )
                        )
                    elif is_lit16 and (lit_val < -32768 or lit_val > 32767):
                        diagnostics.append(
                            SmaliDiagnostic(
                                line_number=line_idx,
                                line_content=raw_line,
                                message=f"Literal value '{lit_str}' ({lit_val}) is out of range for signed 16-bit ({opcode}, valid: -32768 to 32767).",
                                error_code="LITERAL_OUT_OF_RANGE",
                            )
                        )
                except ValueError:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=f"Invalid numeric literal '{lit_str}' for '{opcode}'.",
                            error_code="ILLEGAL_OPERAND",
                        )
                    )
            return None

        # Check three-register operations
        if opcode in _DALVIK_THREE_REG_OPCODES:
            ops = [op.strip() for op in operands.split(",")]
            if len(ops) != 3:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Opcode '{opcode}' requires exactly three register operands (e.g. {opcode} v0, v1, v2).",
                        error_code="ILLEGAL_OPERAND",
                    )
                )
            else:
                is_wide_0 = False
                is_wide_1 = False
                is_wide_2 = False
                if opcode in {
                    "add-long", "sub-long", "mul-long", "div-long", "rem-long",
                    "and-long", "or-long", "xor-long",
                    "add-double", "sub-double", "mul-double", "div-double", "rem-double",
                }:
                    is_wide_0 = is_wide_1 = is_wide_2 = True
                elif opcode in {"shl-long", "shr-long", "ushr-long"}:
                    is_wide_0 = is_wide_1 = True
                    is_wide_2 = False
                elif opcode in {"cmpl-double", "cmpg-double", "cmp-long"}:
                    is_wide_0 = False
                    is_wide_1 = is_wide_2 = True
                elif opcode in {"aget-wide", "aput-wide"}:
                    is_wide_0 = True
                    is_wide_1 = is_wide_2 = False

                cls._check_register(ops[0], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count, is_wide=is_wide_0)
                cls._check_register(ops[1], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count, is_wide=is_wide_1)
                cls._check_register(ops[2], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count, is_wide=is_wide_2)
            return None

        # Check two-register operations
        if (
            opcode in _DALVIK_TWO_REG_OPCODES
            or opcode.startswith("move/")
            or opcode.startswith("move-object/")
            or opcode.startswith("move-wide/")
        ):
            ops = [op.strip() for op in operands.split(",")]
            if len(ops) != 2:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Opcode '{opcode}' requires exactly two register operands (e.g. {opcode} v0, v1).",
                        error_code="ILLEGAL_OPERAND",
                    )
                )
            else:
                is_wide_0 = False
                is_wide_1 = False
                if (
                    opcode.startswith("move-wide")
                    or opcode in {
                        "neg-long", "not-long", "neg-double",
                        "long-to-double", "double-to-long",
                        "add-long/2addr", "sub-long/2addr", "mul-long/2addr", "div-long/2addr", "rem-long/2addr",
                        "and-long/2addr", "or-long/2addr", "xor-long/2addr",
                        "add-double/2addr", "sub-double/2addr", "mul-double/2addr", "div-double/2addr", "rem-double/2addr",
                    }
                ):
                    is_wide_0 = is_wide_1 = True
                elif opcode in {
                    "int-to-long", "float-to-long", "int-to-double", "float-to-double",
                    "shl-long/2addr", "shr-long/2addr", "ushr-long/2addr",
                }:
                    is_wide_0 = True
                    is_wide_1 = False
                elif opcode in {
                    "long-to-int", "long-to-float", "double-to-int", "double-to-float",
                }:
                    is_wide_0 = False
                    is_wide_1 = True

                cls._check_register(
                    ops[0], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count, is_wide=is_wide_0
                )
                cls._check_register(
                    ops[1], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count, is_wide=is_wide_1
                )
            return None

        # Check new-instance / check-cast
        if opcode in {"new-instance", "check-cast"}:
            ops = [op.strip() for op in operands.split(",")]
            if len(ops) != 2:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Opcode '{opcode}' requires one register and one type descriptor (e.g. {opcode} v0, Lclass;).",
                        error_code="ILLEGAL_OPERAND",
                    )
                )
            else:
                cls._check_register(ops[0], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count)
                if not _is_valid_type_descriptor(ops[1], allow_void=False):
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=f"Opcode '{opcode}' requires a valid type descriptor, found '{ops[1]}'.",
                            error_code="INVALID_TYPE_DESCRIPTOR",
                        )
                    )
            return None

        # Check instance-of / new-array
        if opcode in {"instance-of", "new-array"}:
            ops = [op.strip() for op in operands.split(",")]
            if len(ops) != 3:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Opcode '{opcode}' requires two registers and one type descriptor (e.g. {opcode} v0, v1, Lclass;).",
                        error_code="ILLEGAL_OPERAND",
                    )
                )
            else:
                cls._check_register(ops[0], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count)
                cls._check_register(ops[1], line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count)
                type_desc = ops[2]
                if opcode == "new-array" and not type_desc.startswith("["):
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=f"Opcode 'new-array' requires an array type descriptor starting with '[' (e.g. [I, [Ljava/lang/String;), found '{type_desc}'.",
                            error_code="INVALID_TYPE_DESCRIPTOR",
                        )
                    )
                elif not _is_valid_type_descriptor(type_desc, allow_void=False):
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=f"Opcode '{opcode}' requires a valid type descriptor, found '{type_desc}'.",
                            error_code="INVALID_TYPE_DESCRIPTOR",
                        )
                    )
            return None

        # Opcode validity check against known Dalvik opcodes
        if opcode not in _ALL_VALID_DALVIK_OPCODES:
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message=f"Unknown or illegal Dalvik opcode '{opcode}'.",
                    error_code="UNKNOWN_OPCODE",
                )
            )
            return None

        # Generic register and operand checking on comma-separated tokens
        if operands:
            tokens = [t.strip() for t in operands.split(",")]
            for tok in tokens:
                if re.match(r"^[vp]\d+$", tok):
                    cls._check_register(
                        tok, line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count
                    )
                elif re.match(r"^[a-zA-Z]\d+$", tok) and not tok.lower().startswith(("v", "p")) :
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=f"Invalid register identifier '{tok}'. Dalvik only recognizes 'v<N>' or 'p<N>' registers.",
                            error_code="INVALID_REGISTER_NAME",
                        )
                    )

        return None

    @classmethod
    def _validate_move_result(
        cls,
        opcode: str,
        operands: str,
        line_idx: int,
        raw_line: str,
        diagnostics: list[SmaliDiagnostic],
        declared_registers: int | None,
        declared_locals: int | None,
        param_reg_count: int | None,
        last_invoke: tuple[str, str, int] | None,
    ) -> None:
        """Validate move-result instructions for placement and type consistency."""
        if not operands or "," in operands:
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message=f"Opcode '{opcode}' requires exactly one register operand.",
                    error_code="ILLEGAL_OPERAND",
                )
            )
            return

        cls._check_register(
            operands.strip(),
            line_idx,
            raw_line,
            diagnostics,
            declared_registers,
            declared_locals,
            param_reg_count,
        )

        if last_invoke is None:
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message=f"'{opcode}' must immediately follow an invoke-* or filled-new-array instruction.",
                    error_code="ILLEGAL_MOVE_RESULT",
                )
            )
            return

        inv_name, inv_ret, _ = last_invoke
        if inv_ret == "V":
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message=f"Cannot move result of method '{inv_name}' returning void ('V').",
                    error_code="ILLEGAL_MOVE_RESULT",
                )
            )
        elif opcode == "move-result":
            if inv_ret.startswith("L") or inv_ret.startswith("["):
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Method '{inv_name}' returns object type '{inv_ret}'. Use 'move-result-object' instead of 'move-result'.",
                        error_code="ILLEGAL_MOVE_RESULT",
                    )
                )
            elif inv_ret in ("J", "D"):
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Method '{inv_name}' returns 64-bit wide type '{inv_ret}'. Use 'move-result-wide' instead of 'move-result'.",
                        error_code="ILLEGAL_MOVE_RESULT",
                    )
                )
        elif opcode == "move-result-object":
            if not (inv_ret.startswith("L") or inv_ret.startswith("[")):
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Method '{inv_name}' returns primitive type '{inv_ret}'. Use 'move-result' instead of 'move-result-object'.",
                        error_code="ILLEGAL_MOVE_RESULT",
                    )
                )
        elif opcode == "move-result-wide":
            if inv_ret not in ("J", "D"):
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Method '{inv_name}' returns non-wide type '{inv_ret}'. Use 'move-result' instead of 'move-result-wide'.",
                        error_code="ILLEGAL_MOVE_RESULT",
                    )
                )

    @classmethod
    def _check_return_type_match(
        cls,
        opcode: str,
        line_idx: int,
        raw_line: str,
        diagnostics: list[SmaliDiagnostic],
        method_sig: str,
    ) -> None:
        """Check that return instruction matches the declared method return type."""
        if not method_sig:
            return
        m = _METHOD_SIG_RE.search(method_sig)
        if not m:
            return
        _, _, m_ret = m.groups()
        m_ret = m_ret.strip()
        if not m_ret:
            return

        if m_ret == "V":
            if opcode != "return-void":
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Method declared to return void ('V') must use 'return-void', found '{opcode}'.",
                        error_code="ILLEGAL_RETURN",
                    )
                )
        elif m_ret.startswith("L") or m_ret.startswith("["):
            if opcode == "return-void":
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Method declared to return object '{m_ret}' cannot return void.",
                        error_code="ILLEGAL_RETURN",
                    )
                )
            elif opcode != "return-object":
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Method declared to return object '{m_ret}' must use 'return-object', found '{opcode}'.",
                        error_code="ILLEGAL_RETURN",
                    )
                )
        elif m_ret in ("J", "D"):
            if opcode == "return-void":
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Method declared to return wide type '{m_ret}' cannot return void.",
                        error_code="ILLEGAL_RETURN",
                    )
                )
            elif opcode != "return-wide":
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Method declared to return wide type '{m_ret}' must use 'return-wide', found '{opcode}'.",
                        error_code="ILLEGAL_RETURN",
                    )
                )
        elif m_ret in _PRIMITIVE_TYPES:
            if opcode == "return-void":
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Method declared to return primitive '{m_ret}' cannot return void.",
                        error_code="ILLEGAL_RETURN",
                    )
                )
            elif opcode != "return":
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Method declared to return primitive '{m_ret}' must use 'return', found '{opcode}'.",
                        error_code="ILLEGAL_RETURN",
                    )
                )

    @classmethod
    def _validate_invoke_instruction(
        cls,
        code_line: str,
        raw_line: str,
        line_idx: int,
        diagnostics: list[SmaliDiagnostic],
        declared_registers: int | None,
        declared_locals: int | None,
        is_static: bool,
        method_sig: str,
        param_reg_count: int | None,
    ) -> tuple[str, str, int] | None:
        """Validate Dalvik invoke-* instructions."""
        # 1. Validate register list inside {...}
        brace_match = re.search(r"\{([^}]*)\}", code_line)
        if not brace_match:
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message="Malformed invoke instruction: missing register list enclosed in '{...}'.",
                    error_code="MISSING_REGISTER_LIST",
                )
            )
            return None

        reg_list_str = brace_match.group(1).strip()
        is_range = "/range" in code_line.split("{")[0]

        if is_range:
            if reg_list_str:
                if ".." not in reg_list_str:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message="Malformed invoke/range: expected '{vA .. vB}'.",
                            error_code="INVALID_REGISTER_RANGE",
                        )
                    )
                else:
                    parts = [r.strip() for r in reg_list_str.split("..", 1)]
                    r_start, r_end = parts[0], parts[1]
                    m_start = re.match(r"^([vp])(\d+)$", r_start)
                    m_end = re.match(r"^([vp])(\d+)$", r_end)
                    if not m_start or not m_end:
                        cls._check_register_token(
                            r_start, line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count
                        )
                        cls._check_register_token(
                            r_end, line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count
                        )
                    elif m_start.group(1) != m_end.group(1):
                        diagnostics.append(
                            SmaliDiagnostic(
                                line_number=line_idx,
                                line_content=raw_line,
                                message=f"Invalid register range '{{{reg_list_str}}}': cannot mix '{m_start.group(1)}' and '{m_end.group(1)}' registers.",
                                error_code="INVALID_REGISTER_RANGE",
                            )
                        )
                    elif int(m_start.group(2)) > int(m_end.group(2)):
                        diagnostics.append(
                            SmaliDiagnostic(
                                line_number=line_idx,
                                line_content=raw_line,
                                message=f"Invalid register range '{{{reg_list_str}}}': start register {r_start} cannot be greater than end register {r_end}.",
                                error_code="INVALID_REGISTER_RANGE",
                            )
                        )
                    else:
                        cls._check_register_token(
                            r_start, line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count
                        )
                        cls._check_register_token(
                            r_end, line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count
                        )
        elif reg_list_str:
            tokens = [t.strip() for t in reg_list_str.split(",")]
            for tok in tokens:
                cls._check_register_token(
                    tok, line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count
                )

        # 2. Validate method target and return type descriptor
        # After '}', there must be a comma and the method descriptor
        after_brace = code_line[brace_match.end() :].strip()
        if not after_brace.startswith(","):
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message="Malformed invoke instruction: missing comma after register list.",
                    error_code="MALFORMED_INVOKE",
                )
            )
            return None

        # Check Dalvik 038+ invoke-polymorphic instructions
        if any(code_line.startswith(pfx) for pfx in ("invoke-polymorphic", "invoke-polymorphic/range")):
            poly_parts = [p.strip() for p in after_brace[1:].split(",", 1)]
            if len(poly_parts) != 2:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message="Malformed invoke-polymorphic: expected method target and call-site prototype (e.g. Lclass;->method(...)Ret, (proto)Ret).",
                        error_code="MALFORMED_INVOKE",
                    )
                )
                return None
            method_target, proto_str = poly_parts[0], poly_parts[1]
            if "->" not in method_target:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Missing '->' separator in method target '{method_target}'.",
                        error_code="MALFORMED_METHOD_TARGET",
                    )
                )
                return None
            class_part, method_part = method_target.split("->", 1)
            if not (class_part.startswith("L") or class_part.startswith("[")):
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Invalid target class descriptor '{class_part}'. Must start with 'L' or '['.",
                        error_code="INVALID_CLASS_DESCRIPTOR",
                    )
                )
            match = _METHOD_SIG_RE.match(method_part)
            if not match:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Malformed method signature '{method_part}'. Expected 'name(params)ReturnType'.",
                        error_code="MALFORMED_METHOD_SIGNATURE",
                    )
                )
                return None
            m_name, m_params, m_ret = match.groups()
            m_proto = re.match(r"^\(([^)]*)\)(.+)$", proto_str)
            if not m_proto:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Malformed call-site prototype '{proto_str}' in invoke-polymorphic. Expected '(params)ReturnType'.",
                        error_code="MALFORMED_METHOD_SIGNATURE",
                    )
                )
                return None
            p_params, p_ret = m_proto.groups()
            if not _is_valid_type_descriptor(p_ret, allow_void=True):
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Invalid call-site return type descriptor '{p_ret}' in invoke-polymorphic.",
                        error_code="INVALID_RETURN_TYPE",
                    )
                )
            for pt in _split_params(p_params):
                if not _is_valid_type_descriptor(pt, allow_void=False):
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=f"Invalid call-site parameter descriptor '{pt}' in invoke-polymorphic.",
                            error_code="INVALID_PARAM_DESCRIPTOR",
                        )
                    )
            return (m_name, p_ret, line_idx)

        # Check Dalvik 038+ invoke-custom instructions
        if any(code_line.startswith(pfx) for pfx in ("invoke-custom", "invoke-custom/range")):
            cust_parts = [p.strip() for p in after_brace[1:].split(",", 1)]
            if len(cust_parts) != 2:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message="Malformed invoke-custom: expected bootstrap method target and call-site signature (e.g. Lclass;->bsm(...)LCallSite;, method(params)Ret).",
                        error_code="MALFORMED_INVOKE",
                    )
                )
                return None
            bsm_target, call_str = cust_parts[0], cust_parts[1]
            if "->" not in bsm_target:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Missing '->' separator in bootstrap target '{bsm_target}'.",
                        error_code="MALFORMED_METHOD_TARGET",
                    )
                )
                return None
            class_part, method_part = bsm_target.split("->", 1)
            if not (class_part.startswith("L") or class_part.startswith("[")):
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Invalid bootstrap class descriptor '{class_part}'. Must start with 'L' or '['.",
                        error_code="INVALID_CLASS_DESCRIPTOR",
                    )
                )
            match = _METHOD_SIG_RE.match(method_part)
            if not match:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Malformed bootstrap method signature '{method_part}'. Expected 'name(params)ReturnType'.",
                        error_code="MALFORMED_METHOD_SIGNATURE",
                    )
                )
                return None
            m_call = _METHOD_SIG_RE.match(call_str)
            if not m_call:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Malformed call-site signature '{call_str}' in invoke-custom. Expected 'name(params)ReturnType'.",
                        error_code="MALFORMED_METHOD_SIGNATURE",
                    )
                )
                return None
            c_name, c_params, c_ret = m_call.groups()
            if not _is_valid_type_descriptor(c_ret, allow_void=True):
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Invalid call-site return type descriptor '{c_ret}' in invoke-custom.",
                        error_code="INVALID_RETURN_TYPE",
                    )
                )
            for pt in _split_params(c_params):
                if not _is_valid_type_descriptor(pt, allow_void=False):
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=f"Invalid call-site parameter descriptor '{pt}' in invoke-custom.",
                            error_code="INVALID_PARAM_DESCRIPTOR",
                        )
                    )
            return (c_name, c_ret, line_idx)

        method_target = after_brace[1:].strip()
        if not method_target:
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message="Malformed invoke instruction: missing target method reference.",
                    error_code="MALFORMED_INVOKE",
                )
            )
            return None

        if "->" not in method_target:
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message=f"Missing '->' separator in method target '{method_target}'.",
                    error_code="MALFORMED_METHOD_TARGET",
                )
            )
            return None

        class_part, method_part = method_target.split("->", 1)
        if not class_part.startswith("L") and not class_part.startswith("["):
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message=f"Invalid target class descriptor '{class_part}'. Must start with 'L' or '['.",
                    error_code="INVALID_CLASS_DESCRIPTOR",
                )
            )

        match = _METHOD_SIG_RE.match(method_part)
        if not match:
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message=f"Malformed method signature '{method_part}'. Expected 'name(params)ReturnType'.",
                    error_code="MALFORMED_METHOD_SIGNATURE",
                )
            )
            return None

        m_name, m_params, m_ret = match.groups()

        # Constructor invocation check (<init> must be called with invoke-direct or invoke-super)
        if m_name == "<init>":
            allowed_invoke = any(
                code_line.startswith(pfx)
                for pfx in ("invoke-direct", "invoke-super")
            )
            if not allowed_invoke:
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Constructor '<init>' cannot be invoked with '{code_line.split('{')[0].strip()}'. Dalvik requires 'invoke-direct' or 'invoke-super'.",
                        error_code="ILLEGAL_CONSTRUCTOR_INVOCATION",
                    )
                )

        # Check for commas inside parameter descriptors
        if "," in m_params:
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message=f"Invalid parameter descriptor '({m_params})': Dalvik method parameter types must not be separated by commas.",
                    error_code="INVALID_PARAM_DESCRIPTOR",
                )
            )

        # Return type descriptor validation (R1 Acceptance Criteria)
        if not m_ret:
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message=(
                        f"Invoke instruction method target '{method_part}' is missing a return type descriptor "
                        "(e.g. 'V' for void). Omitting the return type breaks APKTool compilation."
                    ),
                    error_code="MISSING_RETURN_TYPE",
                )
            )
        elif not _is_valid_type_descriptor(m_ret, allow_void=True):
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message=f"Invalid return type descriptor '{m_ret}' in method signature. Must be a valid Dalvik type (e.g. 'V', 'I', 'Ljava/lang/String;').",
                    error_code="INVALID_RETURN_TYPE",
                )
            )

        # Parameter descriptor and argument count validation
        param_types = _split_params(m_params)
        for pt in param_types:
            if not _is_valid_type_descriptor(pt, allow_void=False):
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=f"Invalid parameter type descriptor '{pt}' in method signature.",
                        error_code="INVALID_PARAM_DESCRIPTOR",
                    )
                )

        # Standard invoke argument register count check
        is_invoke_static = "invoke-static" in code_line.split("{")[0]
        is_standard_invoke = any(
            code_line.startswith(pfx)
            for pfx in ("invoke-virtual", "invoke-super", "invoke-direct", "invoke-static", "invoke-interface")
        )
        if is_standard_invoke and not any(d.error_code == "INVALID_PARAM_DESCRIPTOR" for d in diagnostics):
            expected_arg_regs = (0 if is_invoke_static else 1) + sum(_param_register_count(p) for p in param_types)
            if is_range:
                if reg_list_str and ".." in reg_list_str:
                    parts = [r.strip() for r in reg_list_str.split("..", 1)]
                    ms = re.match(r"^[vp](\d+)$", parts[0])
                    me = re.match(r"^[vp](\d+)$", parts[1])
                    if ms and me:
                        actual_count = int(me.group(1)) - int(ms.group(1)) + 1
                        if actual_count >= 0 and actual_count != expected_arg_regs:
                            diagnostics.append(
                                SmaliDiagnostic(
                                    line_number=line_idx,
                                    line_content=raw_line,
                                    message=f"Method '{m_name}' expects {expected_arg_regs} register argument(s), but range '{{{reg_list_str}}}' provides {actual_count}.",
                                    error_code="ARGUMENT_COUNT_MISMATCH",
                                )
                            )
            else:
                toks = [t.strip() for t in reg_list_str.split(",") if t.strip()] if reg_list_str else []
                if len(toks) != expected_arg_regs:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message=(
                                f"Method '{m_name}' expects {expected_arg_regs} register argument(s) "
                                f"({'static' if is_invoke_static else 'non-static including this'}), but found {len(toks)}."
                            ),
                            error_code="ARGUMENT_COUNT_MISMATCH",
                        )
                    )

        return (m_name, m_ret, line_idx)

    @classmethod
    def _validate_filled_new_array(
        cls,
        code_line: str,
        raw_line: str,
        line_idx: int,
        diagnostics: list[SmaliDiagnostic],
        declared_registers: int | None,
        declared_locals: int | None,
        param_reg_count: int | None,
    ) -> str | None:
        """Validate filled-new-array / filled-new-array/range instruction."""
        brace_match = re.search(r"\{([^}]*)\}", code_line)
        if not brace_match:
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message="Malformed filled-new-array instruction: missing register list '{...}'.",
                    error_code="MISSING_REGISTER_LIST",
                )
            )
            return None

        is_range = "/range" in code_line.split("{")[0]
        reg_list_str = brace_match.group(1).strip()
        if is_range:
            if reg_list_str:
                if ".." not in reg_list_str:
                    diagnostics.append(
                        SmaliDiagnostic(
                            line_number=line_idx,
                            line_content=raw_line,
                            message="Malformed filled-new-array/range: expected '{vA .. vB}'.",
                            error_code="INVALID_REGISTER_RANGE",
                        )
                    )
                else:
                    parts = [r.strip() for r in reg_list_str.split("..", 1)]
                    r_start, r_end = parts[0], parts[1]
                    cls._check_register(r_start, line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count)
                    cls._check_register(r_end, line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count)
        elif reg_list_str:
            tokens = [t.strip() for t in reg_list_str.split(",")]
            for tok in tokens:
                cls._check_register_token(
                    tok, line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count
                )

        after_brace = code_line[brace_match.end() :].strip()
        if not after_brace.startswith(","):
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message="Malformed filled-new-array: missing comma and array type descriptor after register list.",
                    error_code="ILLEGAL_OPERAND",
                )
            )
            return None

        array_type = after_brace[1:].strip()
        if not array_type:
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message="Missing array type descriptor in filled-new-array instruction.",
                    error_code="MISSING_FIELD_TYPE",
                )
            )
            return None

        if not array_type.startswith("[") or not _is_valid_type_descriptor(array_type, allow_void=False):
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message=f"Opcode 'filled-new-array' requires an array type descriptor starting with '[' (e.g. [I, [Ljava/lang/String;), found '{array_type}'.",
                    error_code="INVALID_TYPE_DESCRIPTOR",
                )
            )
            return None

        return array_type

    @classmethod
    def _check_register_token(
        cls,
        token: str,
        line_idx: int,
        raw_line: str,
        diagnostics: list[SmaliDiagnostic],
        declared_registers: int | None,
        declared_locals: int | None,
        param_reg_count: int | None = None,
    ) -> None:
        """Verify that a token in a register list is a valid register, not a raw literal."""
        token = token.strip()
        if not token:
            return

        # Check for raw string literals: e.g. "https://example.com"
        if token.startswith('"') or token.startswith("'"):
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message=(
                        f"Literal string {token} found inside register argument list. "
                        "Dalvik bytecode requires registers (e.g. {v0}), not raw string literals. "
                        "Load string into a register via const-string first."
                    ),
                    error_code="LITERAL_IN_REGISTER_LIST",
                )
            )
            return

        # Check for numeric literals: e.g. 123, 0x1, -1
        if re.match(r"^[+-]?(?:0x[0-9a-fA-F]+|\d+)$", token):
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message=(
                        f"Literal number {token} found inside register argument list. "
                        "Dalvik bytecode requires registers (e.g. {v0}), not raw numbers. "
                        "Load numeric constant via const/4 or const-wide first."
                    ),
                    error_code="LITERAL_IN_REGISTER_LIST",
                )
            )
            return

        cls._check_register(
            token, line_idx, raw_line, diagnostics, declared_registers, declared_locals, param_reg_count
        )

    @classmethod
    def _check_register(
        cls,
        reg: str,
        line_idx: int,
        raw_line: str,
        diagnostics: list[SmaliDiagnostic],
        declared_registers: int | None,
        declared_locals: int | None,
        param_reg_count: int | None = None,
        is_wide: bool = False,
    ) -> None:
        """Verify register naming and allocated bounds."""
        reg = reg.strip()
        m = re.match(r"^([vp])(\d+)$", reg)
        if not m:
            error_code = "INVALID_REGISTER_NAME" if re.match(r"^[a-zA-Z]\d+$", reg) else "INVALID_REGISTER"
            diagnostics.append(
                SmaliDiagnostic(
                    line_number=line_idx,
                    line_content=raw_line,
                    message=f"Invalid register identifier '{reg}'. Dalvik only recognizes 'v<N>' (local) or 'p<N>' (parameter) registers.",
                    error_code=error_code,
                )
            )
            return

        reg_type = m.group(1)
        reg_num = int(m.group(2))
        regs_needed = 2 if is_wide else 1

        # Check bounds against .registers directive
        if declared_registers is not None:
            if reg_type == "v" and (reg_num + regs_needed - 1) >= declared_registers:
                msg = (
                    f"Wide register pair '{reg}:v{reg_num + 1}' is out of range for 64-bit operation. Declared .registers is {declared_registers}."
                    if is_wide
                    else (
                        f"Register '{reg}' is out of range. Declared .registers is {declared_registers} "
                        f"(valid local registers: v0..v{declared_registers - 1})."
                    )
                )
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=msg,
                        error_code="OUT_OF_RANGE_REGISTER",
                    )
                )

        # Check bounds against .locals directive
        if declared_locals is not None:
            total_v_allowed = declared_locals + (param_reg_count or 0)
            if reg_type == "v" and (reg_num + regs_needed - 1) >= total_v_allowed:
                msg = (
                    f"Wide register pair '{reg}:v{reg_num + 1}' is out of range for 64-bit operation. Declared .locals is {declared_locals}."
                    if is_wide
                    else (
                        f"Register '{reg}' is out of range. Declared .locals is {declared_locals} "
                        f"(valid local registers: v0..v{total_v_allowed - 1})."
                    )
                )
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=msg,
                        error_code="OUT_OF_RANGE_REGISTER",
                    )
                )

        # Check bounds for parameter registers (p0, p1, ...)
        if reg_type == "p" and param_reg_count is not None:
            if (reg_num + regs_needed - 1) >= param_reg_count:
                msg = (
                    f"Wide parameter register pair '{reg}:p{reg_num + 1}' is out of range for 64-bit operation. Method declares {param_reg_count} parameter register(s)."
                    if is_wide
                    else (
                        f"Parameter register '{reg}' is out of range. Method declares {param_reg_count} "
                        f"parameter register(s) (valid parameter registers: p0..p{param_reg_count - 1})."
                    )
                )
                diagnostics.append(
                    SmaliDiagnostic(
                        line_number=line_idx,
                        line_content=raw_line,
                        message=msg,
                        error_code="OUT_OF_RANGE_REGISTER",
                    )
                )

    @classmethod
    def _extract_enclosing_context(
        cls,
        enclosing_content: str,
        method_sig: str,
        out_state: dict[str, Any],
    ) -> None:
        """Inspect enclosing Smali file to discover method register allocation."""
        lines = enclosing_content.splitlines()
        found_target = False
        for line in lines:
            line_str = line.strip()
            if line_str.startswith(".method") and method_sig in line_str:
                found_target = True
                out_state["static"] = " static " in f" {line_str} "
                continue
            if found_target:
                if line_str.startswith(":"):
                    out_state.setdefault("labels", set()).add(line_str.split()[0])
                elif line_str.startswith(".locals"):
                    parts = line_str.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        out_state["locals"] = int(parts[1])
                elif line_str.startswith(".registers"):
                    parts = line_str.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        out_state["registers"] = int(parts[1])
                elif line_str.startswith(".end method"):
                    break
