"""Adversarial tests for Dalvik Smali Bytecode Pre-flight Validator (Round 2)."""

from noir.patches.smali_validator import SmaliBytecodeValidator


def test_validator_detects_undefined_label_in_packed_switch():
    """packed-switch referencing undefined data table label is rejected."""
    smali = """
.method public testSwitch()V
    .locals 2
    packed-switch v0, :pswitch_data_missing
    return-void
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert not result.is_valid
    codes = [d.error_code for d in result.diagnostics]
    assert "UNDEFINED_LABEL" in codes
    assert any("pswitch_data_missing" in d.message for d in result.diagnostics)


def test_validator_detects_undefined_label_in_sparse_switch():
    """sparse-switch referencing undefined data table label is rejected."""
    smali = """
.method public testSwitch()V
    .locals 2
    sparse-switch v0, :sswitch_data_missing
    return-void
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert not result.is_valid
    assert any(d.error_code == "UNDEFINED_LABEL" for d in result.diagnostics)


def test_validator_detects_undefined_case_label_in_sparse_switch_payload():
    """sparse-switch payload referencing undefined case target label is rejected."""
    smali = """
.method public testSwitch()V
    .locals 2
    sparse-switch v0, :sswitch_data_0
    return-void

    :sswitch_data_0
    .sparse-switch
        0x1 -> :case_target_missing
    .end sparse-switch
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert not result.is_valid
    assert any(d.error_code == "UNDEFINED_LABEL" and "case_target_missing" in d.message for d in result.diagnostics)


def test_validator_detects_undefined_label_in_fill_array_data():
    """fill-array-data referencing undefined array data table is rejected."""
    smali = """
.method public testArray()V
    .locals 2
    fill-array-data v0, :array_missing
    return-void
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert not result.is_valid
    assert any(d.error_code == "UNDEFINED_LABEL" and "array_missing" in d.message for d in result.diagnostics)


def test_validator_detects_undefined_label_in_catch_and_catchall():
    """.catch and .catchall referencing undefined handler labels are rejected."""
    smali = """
.method public testTryCatch()V
    .locals 2
    .catch Ljava/lang/Exception; {:try_start .. :try_end} :catch_missing
    .catchall {:try_start .. :try_end} :catchall_missing
    :try_start
    nop
    :try_end
    return-void
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert not result.is_valid
    diag_messages = " ".join(d.message for d in result.diagnostics)
    assert "catch_missing" in diag_messages
    assert "catchall_missing" in diag_messages


def test_validator_ignores_annotation_contents_without_false_positives():
    """Annotation metadata blocks are parsed without false positive instruction diagnostics."""
    smali = """
.method public testAnnotated()V
    .locals 2
    .annotation system Ldalvik/annotation/Signature;
        value = {
            "Ljava/util/List<",
            "Ljava/lang/String;",
            ">;"
        }
    .end annotation
    return-void
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid


def test_validator_detects_missing_field_type_descriptor():
    """Field access missing type descriptor is rejected."""
    smali_sget = """
.method public testField()V
    .locals 2
    sget-object v0, Ljava/lang/System;->out
    return-void
.end method
"""
    result = SmaliBytecodeValidator.validate(smali_sget)
    assert not result.is_valid
    assert any(d.error_code == "MISSING_FIELD_TYPE" for d in result.diagnostics)


def test_validator_detects_invalid_field_type_descriptor():
    """Field access with invalid type descriptor is rejected."""
    smali = """
.method public testField()V
    .locals 2
    iget-object v0, v1, Lcom/example/Test;->myField:InvalidType
    return-void
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert not result.is_valid
    assert any(d.error_code == "INVALID_FIELD_TYPE" for d in result.diagnostics)


def test_validator_detects_wide_register_overflow():
    """64-bit wide operations requiring 2 registers are checked against register allocation bounds."""
    # .registers 2 allocates v0 and v1. const-wide v1 requires v1 and v2, so v2 overflows!
    smali = """
.method public testWide()V
    .registers 2
    const-wide v1, 0x1000L
    return-void
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert not result.is_valid
    assert any(d.error_code == "OUT_OF_RANGE_REGISTER" for d in result.diagnostics)


def test_validator_detects_lit8_overflow():
    """add-int/lit8 literal exceeding signed 8-bit range is rejected."""
    smali = """
.method public testLit8()V
    .locals 2
    add-int/lit8 v0, v1, 200
    return-void
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert not result.is_valid
    assert any(d.error_code == "LITERAL_OUT_OF_RANGE" for d in result.diagnostics)


def test_validator_detects_lit16_overflow():
    """add-int/lit16 literal exceeding signed 16-bit range is rejected."""
    smali = """
.method public testLit16()V
    .locals 2
    add-int/lit16 v0, v1, 40000
    return-void
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert not result.is_valid
    assert any(d.error_code == "LITERAL_OUT_OF_RANGE" for d in result.diagnostics)


def test_validator_detects_const_high16_non_zero_lower_bits():
    """const/high16 with non-zero lower 16 bits is rejected."""
    smali = """
.method public testHigh16()V
    .locals 2
    const/high16 v0, 0x12345678
    return-void
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert not result.is_valid
    assert any(d.error_code == "LITERAL_OUT_OF_RANGE" for d in result.diagnostics)


def test_validator_detects_init_constructor_called_with_invoke_static():
    """Constructor <init> invoked via invoke-static is rejected."""
    smali = """
.method public testConstructor()V
    .locals 2
    invoke-static {}, Lcom/example/MyClass;-><init>()V
    return-void
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert not result.is_valid
    assert any(d.error_code == "ILLEGAL_CONSTRUCTOR_INVOCATION" for d in result.diagnostics)


def test_validator_detects_move_missing_operand():
    """move instruction with only 1 register operand is rejected."""
    smali = """
.method public testMove()V
    .locals 2
    move v0
    return-void
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert not result.is_valid
    assert any(d.error_code == "ILLEGAL_OPERAND" for d in result.diagnostics)


def test_validator_detects_instance_of_missing_operand():
    """instance-of instruction missing register is rejected."""
    smali = """
.method public testInstanceOf()V
    .locals 2
    instance-of v0, Ljava/lang/String;
    return-void
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert not result.is_valid
    assert any(d.error_code == "ILLEGAL_OPERAND" for d in result.diagnostics)
