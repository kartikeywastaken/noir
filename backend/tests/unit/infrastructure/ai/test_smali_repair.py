"""Unit tests for Smali pre-flight self-repair retry loop."""

import json
import pytest
from noir.domain.enums import PatchOperationType, Provenance
from noir.domain.models import PatchOperation, PatchSet
from noir.infrastructure.ai.repair import (
    SmaliRepairError,
    repair_patch_set,
    repair_smali_operation,
)
from noir.patches.smali_validator import SmaliBytecodeValidator


def test_repair_smali_operation_success():
    # Broken operation: raw string literal inside register brackets
    broken_code = """
    invoke-static {"https://example.com"}, Landroid/net/Uri;->parse(Ljava/lang/String;)Landroid/net/Uri;
    move-result-object v0
"""
    op = PatchOperation(
        relative_path="smali/com/example/MainActivity.smali",
        operation=PatchOperationType.SMALI_INSERT_AT_ANCHOR,
        anchor="# anchor",
        class_descriptor="Lcom/example/MainActivity;",
        method_signature="onCreate(Landroid/os/Bundle;)V",
        new_content=broken_code,
    )

    initial_val = SmaliBytecodeValidator.validate(broken_code)
    assert initial_val.is_valid is False

    # Mock model call that fixes the violation
    call_log = []

    def mock_call_model(prompt: str, system: str) -> str:
        call_log.append((prompt, system))
        assert "LITERAL_IN_REGISTER_LIST" in prompt
        assert '{"https://example.com"}' in prompt
        repaired_code = """
    const-string v0, "https://example.com"
    invoke-static {v0}, Landroid/net/Uri;->parse(Ljava/lang/String;)Landroid/net/Uri;
    move-result-object v0
"""
        return json.dumps({"repaired_new_content": repaired_code})

    repaired_op = repair_smali_operation(
        op,
        initial_val,
        call_model=mock_call_model,
        max_retries=2,
    )

    assert len(call_log) == 1
    assert "const-string v0" in repaired_op.new_content
    # The repaired operation is verified valid
    assert SmaliBytecodeValidator.validate(repaired_op.new_content).is_valid is True


def test_repair_missing_return_type_descriptor():
    broken_code = """
    const-string v0, "Patched"
    invoke-virtual {v0}, Landroid/widget/Toast;->show()
"""
    op = PatchOperation(
        relative_path="smali/com/example/MainActivity.smali",
        operation=PatchOperationType.SMALI_INSERT_AT_ANCHOR,
        anchor="# anchor",
        new_content=broken_code,
    )
    initial_val = SmaliBytecodeValidator.validate(broken_code)
    assert any(d.error_code == "MISSING_RETURN_TYPE" for d in initial_val.diagnostics)

    def mock_call_model(prompt: str, system: str) -> str:
        assert "MISSING_RETURN_TYPE" in prompt
        repaired_code = """
    const-string v0, "Patched"
    invoke-virtual {v0}, Landroid/widget/Toast;->show()V
"""
        return json.dumps({"repaired_new_content": repaired_code})

    repaired_op = repair_smali_operation(op, initial_val, call_model=mock_call_model)
    assert "Toast;->show()V" in repaired_op.new_content
    assert SmaliBytecodeValidator.validate(repaired_op.new_content).is_valid is True


def test_repair_exhausted_retries_raises_without_corruption():
    broken_code = "invoke-static {\"bad\"}, Lfoo;->bar()V"
    op = PatchOperation(
        relative_path="smali/com/example/MainActivity.smali",
        operation=PatchOperationType.SMALI_INSERT_AT_ANCHOR,
        anchor="# anchor",
        new_content=broken_code,
    )
    initial_val = SmaliBytecodeValidator.validate(broken_code)

    def stubborn_model(prompt: str, system: str) -> str:
        # Returns code that is still invalid
        return json.dumps({"repaired_new_content": "invoke-static {123}, Lfoo;->bar()V"})

    with pytest.raises(SmaliRepairError) as exc_info:
        repair_smali_operation(op, initial_val, call_model=stubborn_model, max_retries=2)

    assert "Smali pre-flight self-repair failed after 2 attempts" in str(exc_info.value)
    # Original operation was not corrupted
    assert op.new_content == broken_code


def test_repair_patch_set_leaves_valid_untouched():
    valid_op = PatchOperation(
        relative_path="AndroidManifest.xml",
        operation=PatchOperationType.REPLACE_BLOCK,
        match_content="old",
        new_content="new",
    )
    broken_smali_op = PatchOperation(
        relative_path="smali/com/example/MainActivity.smali",
        operation=PatchOperationType.SMALI_INSERT_AT_ANCHOR,
        anchor="# anchor",
        new_content="invoke-virtual {v0}, Landroid/widget/Toast;->show()",
    )

    patch = PatchSet(
        plan_id="p1",
        project_id="proj1",
        workspace_revision=0,
        provenance=Provenance.AI_GENERATED,
        operations=[valid_op, broken_smali_op],
    )

    def mock_call(prompt: str, system: str) -> str:
        return json.dumps({
            "repaired_new_content": "invoke-virtual {v0}, Landroid/widget/Toast;->show()V"
        })

    repaired = repair_patch_set(patch, call_model=mock_call)
    assert repaired.operations[0].new_content == "new"
    assert "show()V" in repaired.operations[1].new_content


def test_repair_multi_diagnostic_and_markdown_response():
    """Model returning markdown-wrapped json with multiple compiler diagnostics."""
    broken_code = """
    const/4 v0, 0x100
    invoke-static {v0, v1}, Ljava/lang/String;->valueOf(I)Ljava/lang/String;
    move-result-wide v0
"""
    op = PatchOperation(
        relative_path="smali/com/example/MainActivity.smali",
        operation=PatchOperationType.SMALI_INSERT_AT_ANCHOR,
        anchor="# anchor",
        new_content=broken_code,
    )
    initial_val = SmaliBytecodeValidator.validate(broken_code)
    assert initial_val.is_valid is False
    # Expect diagnostics for literal out of range, argument count mismatch, move-result mismatch
    assert any(d.error_code == "LITERAL_OUT_OF_RANGE" for d in initial_val.diagnostics)
    assert any(d.error_code == "ARGUMENT_COUNT_MISMATCH" for d in initial_val.diagnostics)

    def mock_markdown_call(prompt: str, system: str) -> str:
        # Returns markdown code fenced JSON
        return """```json
{
  "repaired_new_content": "    const/4 v0, 0x1\\n    invoke-static {v0}, Ljava/lang/String;->valueOf(I)Ljava/lang/String;\\n    move-result-object v0\\n",
  "explanation": "Fixed const/4 overflow, adjusted argument register count to 1, and used move-result-object for String return."
}
```"""

    repaired_op = repair_smali_operation(op, initial_val, call_model=mock_markdown_call)
    assert "const/4 v0, 0x1" in repaired_op.new_content
    assert SmaliBytecodeValidator.validate(repaired_op.new_content).is_valid is True
