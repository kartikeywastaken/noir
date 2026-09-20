"""Adversarial unit tests for Dalvik Smali validator and bytecode rules."""

import pytest
from noir.patches.smali_validator import SmaliBytecodeValidator


def test_invoke_parameter_count_mismatch_static():
    """Static invoke with 2 registers when method takes 1 integer."""
    smali = """
    invoke-static {v0, v1}, Ljava/lang/String;->valueOf(I)Ljava/lang/String;
    """
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code in ("ARGUMENT_COUNT_MISMATCH", "PARAMETER_COUNT_MISMATCH") for d in result.diagnostics)


def test_invoke_parameter_count_mismatch_virtual():
    """Virtual invoke missing 'this' register or parameter register."""
    smali = """
    # equals(Object) takes this + 1 arg = 2 registers. Only 1 provided.
    invoke-virtual {v0}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    """
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code in ("ARGUMENT_COUNT_MISMATCH", "PARAMETER_COUNT_MISMATCH") for d in result.diagnostics)


def test_invoke_wide_parameter_register_count():
    """Method taking long (J) requires 2 registers for the parameter."""
    smali = """
    # Thread.sleep(J) takes 2 registers for the long primitive.
    invoke-static {v0}, Ljava/lang/Thread;->sleep(J)V
    """
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code in ("ARGUMENT_COUNT_MISMATCH", "PARAMETER_COUNT_MISMATCH") for d in result.diagnostics)


def test_invoke_range_reversed_registers():
    """invoke/range with start register > end register."""
    smali = """
    invoke-virtual/range {v4 .. v1}, Lcom/example/App;->test()V
    """
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code in ("INVALID_REGISTER_RANGE", "ILLEGAL_OPERAND") for d in result.diagnostics)


def test_invoke_range_mixed_register_types():
    """invoke/range cannot mix v and p registers across the range."""
    smali = """
    invoke-virtual/range {v0 .. p1}, Lcom/example/App;->test()V
    """
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code in ("INVALID_REGISTER_RANGE", "ILLEGAL_OPERAND") for d in result.diagnostics)


def test_move_result_without_preceding_invoke():
    """move-result cannot appear arbitrarily without preceding invoke."""
    smali = """
    const/4 v1, 0x0
    move-result v0
    """
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code in ("ILLEGAL_MOVE_RESULT", "ILLEGAL_OPERAND") for d in result.diagnostics)


def test_move_result_on_void_method():
    """move-result on a method returning void (V) is invalid."""
    smali = """
    invoke-virtual {v0}, Landroid/widget/Toast;->show()V
    move-result v0
    """
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code in ("ILLEGAL_MOVE_RESULT", "ILLEGAL_OPERAND") for d in result.diagnostics)


def test_move_result_type_mismatch_object_vs_primitive():
    """move-result used when method returns an object (requires move-result-object)."""
    smali = """
    invoke-static {v0}, Ljava/lang/String;->valueOf(I)Ljava/lang/String;
    move-result v0
    """
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code in ("ILLEGAL_MOVE_RESULT", "TYPE_MISMATCH") for d in result.diagnostics)


def test_return_void_in_non_void_method():
    """Method declared to return an Object or primitive cannot return-void."""
    smali = """
.method public static getString()Ljava/lang/String;
    .registers 1
    return-void
.end method
    """
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code in ("ILLEGAL_RETURN", "TYPE_MISMATCH") for d in result.diagnostics)


def test_return_object_in_primitive_method():
    """Method declared to return int (I) cannot return-object."""
    smali = """
.method public static getInt()I
    .registers 1
    const/4 v0, 0x1
    return-object v0
.end method
    """
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code in ("ILLEGAL_RETURN", "TYPE_MISMATCH") for d in result.diagnostics)


def test_invalid_register_name_in_second_operand():
    """Invalid register name (like r1) in secondary operands must be caught."""
    smali = """
    move v0, r1
    """
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code == "INVALID_REGISTER_NAME" for d in result.diagnostics)


def test_out_of_range_parameter_register():
    """p register exceeding actual method parameter count is rejected."""
    smali = """
.method public static testSingleParam(I)V
    .registers 1
    # Method is static and has 1 parameter (p0). p5 is out of range!
    const/4 p5, 0x1
    return-void
.end method
    """
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code == "OUT_OF_RANGE_REGISTER" for d in result.diagnostics)


def test_const_4_overflow():
    """const/4 only supports 4-bit signed literals (-8 to 7). 0x100 is out of bounds."""
    smali = """
    const/4 v0, 0x100
    """
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code in ("LITERAL_OUT_OF_RANGE", "ILLEGAL_OPERAND") for d in result.diagnostics)


def test_array_of_void_descriptor_rejected():
    """[V is an invalid Dalvik descriptor."""
    smali = """
    new-array v0, v0, [V
    """
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code in ("INVALID_TYPE_DESCRIPTOR", "ILLEGAL_OPERAND") for d in result.diagnostics)


def test_enclosing_file_context_registers_checked():
    """Validating snippet with enclosing file content should catch out-of-range registers."""
    enclosing = """
.method public static targetMethod()V
    .locals 2
    const/4 v0, 0x1
    return-void
.end method
    """
    snippet = """
    const/4 v10, 0x1
    """
    result = SmaliBytecodeValidator.validate(
        snippet,
        context_method="targetMethod()V",
        enclosing_file_content=enclosing,
    )
    assert result.is_valid is False
    assert any(d.error_code == "OUT_OF_RANGE_REGISTER" for d in result.diagnostics)
