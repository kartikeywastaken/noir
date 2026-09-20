"""Compiler-Feedback Self-Repair Retry Loop for Smali patch operations.

When pre-flight validation detects Dalvik bytecode syntax or assembly errors,
automatically triggers a targeted self-repair loop providing the model with
the exact compiler diagnostics, the offending lines, and method context.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from noir.domain.models import PatchOperation, PatchSet
from noir.patches.smali_validator import SmaliBytecodeValidator, SmaliValidationResult

logger = logging.getLogger(__name__)


class SmaliRepairError(Exception):
    """Raised when Smali self-repair retry loop fails to produce valid bytecode."""
    pass


REPAIR_SYSTEM_PROMPT = (
    "You are an expert Dalvik Smali bytecode compiler diagnostic repair specialist. "
    "Your job is to correct syntax, opcode, register, and assembly errors in Dalvik Smali "
    "patch operations based on exact compiler/parser diagnostic messages. "
    "Produce syntactically valid Smali assembly. "
    "Output only valid JSON matching the requested format."
)


def _format_diagnostics_prompt(validation: SmaliValidationResult) -> str:
    """Format structured compiler diagnostics for the model."""
    lines = []
    for d in validation.diagnostics:
        lines.append(
            f"- Line {d.line_number}: [{d.error_code}] {d.message}\n"
            f"  Offending line: {d.line_content}"
        )
    return "\n".join(lines)


def repair_smali_operation(
    operation: PatchOperation,
    validation: SmaliValidationResult,
    *,
    call_model: Callable[[str, str], str],
    max_retries: int = 2,
    context: dict[str, Any] | None = None,
) -> PatchOperation:
    """Run targeted self-repair retry loop on an invalid Smali patch operation.

    Args:
        operation: The PatchOperation with invalid Smali bytecode.
        validation: The SmaliValidationResult containing line-level diagnostics.
        call_model: Function to send prompt and system prompt to the LLM.
        max_retries: Maximum number of repair iterations to attempt.
        context: Optional workspace context.

    Returns:
        Repaired PatchOperation with verified valid Smali bytecode.

    Raises:
        SmaliRepairError: If the repair fails after max_retries without corrupting workspace.
    """
    current_op = operation.model_copy(deep=True)
    current_validation = validation

    for attempt in range(1, max_retries + 1):
        logger.info(
            "Smali self-repair attempt %d/%d for %s (%s)",
            attempt,
            max_retries,
            current_op.relative_path,
            current_op.method_signature or current_op.anchor or "smali",
        )

        prompt = (
            f"The Dalvik Smali pre-flight bytecode validator detected compilation/syntax errors "
            f"in a generated patch operation.\n\n"
            f"OPERATION DETAILS:\n"
            f"- File: {current_op.relative_path}\n"
            f"- Operation type: {current_op.operation.value}\n"
            f"- Target Class: {current_op.class_descriptor or 'N/A'}\n"
            f"- Target Method: {current_op.method_signature or 'N/A'}\n"
            f"- Anchor: {current_op.anchor or 'N/A'}\n\n"
            f"EXACT COMPILER / PARSER DIAGNOSTICS:\n"
            f"{_format_diagnostics_prompt(current_validation)}\n\n"
            f"OFFENDING SMALI CODE:\n"
            f"```smali\n"
            f"{current_op.new_content or ''}\n"
            f"```\n\n"
            f"CRITICAL FIX REQUIREMENTS:\n"
            f"1. Never put raw string or numeric literals inside {{}} in invoke instructions (e.g. {{ \"https://...\" }} is illegal). Load into a register first using const-string or const/4, then pass the register.\n"
            f"2. Method target signatures in invoke instructions MUST include the return descriptor, including 'V' for void (e.g. Landroid/widget/Toast;->show()V). Never omit trailing 'V'.\n"
            f"3. All registers used must be within the declared .locals or .registers count. Update .locals or .registers if needed.\n"
            f"4. return-void takes no arguments.\n\n"
            f"Return JSON:\n"
            f"{{\n"
            f'  "repaired_new_content": "<corrected smali code>",\n'
            f'  "explanation": "<brief explanation of fix>"\n'
            f"}}"
        )

        try:
            response_text = call_model(prompt, REPAIR_SYSTEM_PROMPT)
            repaired_content = _extract_repaired_content(response_text)
        except Exception as exc:
            logger.warning("Smali repair attempt %d failed to call model: %s", attempt, exc)
            if attempt == max_retries:
                raise SmaliRepairError(f"Model call failed during Smali self-repair: {exc}") from exc
            continue

        if not repaired_content:
            logger.warning("Smali repair attempt %d returned empty content", attempt)
            continue

        # Re-validate repaired content
        test_val = SmaliBytecodeValidator.validate(
            repaired_content,
            context_method=current_op.method_signature,
            context_class=current_op.class_descriptor,
        )

        if test_val.is_valid:
            logger.info("Smali self-repair succeeded on attempt %d", attempt)
            current_op.new_content = repaired_content
            return current_op

        # Update diagnostics for next attempt
        current_op.new_content = repaired_content
        current_validation = test_val
        logger.warning(
            "Smali self-repair attempt %d produced remaining syntax errors: %s",
            attempt,
            test_val.error_summary,
        )

    # If all attempts exhausted
    error_summary = current_validation.error_summary
    logger.error("Smali self-repair failed after %d attempts. Summary: %s", max_retries, error_summary)
    raise SmaliRepairError(
        f"Smali pre-flight self-repair failed after {max_retries} attempts: {error_summary}"
    )


def repair_patch_set(
    patch: PatchSet,
    *,
    call_model: Callable[[str, str], str],
    max_retries: int = 2,
    context: dict[str, Any] | None = None,
) -> PatchSet:
    """Scan and self-repair all Smali operations in a PatchSet."""
    repaired_ops = []
    any_repaired = False

    for idx, op in enumerate(patch.operations):
        is_smali_op = (
            op.operation.value.startswith("smali_")
            or (op.relative_path.endswith(".smali") and bool(op.new_content))
        )
        if is_smali_op and op.new_content:
            val = SmaliBytecodeValidator.validate(
                op.new_content,
                context_method=op.method_signature,
                context_class=op.class_descriptor,
            )
            if not val.is_valid:
                logger.warning(
                    "Smali syntax errors detected in patch operation %d (%s). Initiating repair loop...",
                    idx + 1,
                    op.relative_path,
                )
                repaired_op = repair_smali_operation(
                    op,
                    val,
                    call_model=call_model,
                    max_retries=max_retries,
                    context=context,
                )
                repaired_ops.append(repaired_op)
                any_repaired = True
                continue

        repaired_ops.append(op)

    if any_repaired:
        return PatchSet(
            plan_id=patch.plan_id,
            project_id=patch.project_id,
            workspace_revision=patch.workspace_revision,
            provenance=patch.provenance,
            operations=repaired_ops,
        )
    return patch


def _extract_repaired_content(response_text: str) -> str:
    """Extract repaired_new_content from model JSON response."""
    text = response_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove opening and closing markdown fences
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            if "repaired_new_content" in data:
                return str(data["repaired_new_content"])
            if "new_content" in data:
                return str(data["new_content"])
            if "operations" in data and isinstance(data["operations"], list) and data["operations"]:
                first = data["operations"][0]
                if isinstance(first, dict) and "new_content" in first:
                    return str(first["new_content"])
    except json.JSONDecodeError:
        pass

    # If response is raw smali
    if ".method" in text or "invoke-" in text or "const-" in text:
        return text

    return ""
