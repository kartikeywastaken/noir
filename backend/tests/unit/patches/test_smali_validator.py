"""Unit tests for Dalvik Smali bytecode pre-flight validator."""

from noir.patches.smali_validator import SmaliBytecodeValidator


def test_valid_smali_method_passes():
    smali = """
.method public static testValidMethod(Landroid/content/Context;)V
    .registers 3
    const-string v0, "Hello World"
    const/4 v1, 0x0
    invoke-static {p0, v0, v1}, Landroid/widget/Toast;->makeText(Landroid/content/Context;Ljava/lang/CharSequence;I)Landroid/widget/Toast;
    move-result-object v0
    invoke-virtual {v0}, Landroid/widget/Toast;->show()V
    return-void
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is True
    assert len(result.diagnostics) == 0


def test_literal_string_in_register_list_rejected():
    smali = """
    const-string v1, "GET"
    invoke-static {"https://example.com"}, Landroid/net/Uri;->parse(Ljava/lang/String;)Landroid/net/Uri;
    move-result-object v0
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code == "LITERAL_IN_REGISTER_LIST" for d in result.diagnostics)
    diag = next(d for d in result.diagnostics if d.error_code == "LITERAL_IN_REGISTER_LIST")
    assert diag.line_number == 3
    assert '{"https://example.com"}' in diag.line_content or "https://example.com" in diag.message


def test_literal_numeric_in_register_list_rejected():
    smali = """
    invoke-static {123}, Ljava/lang/String;->valueOf(I)Ljava/lang/String;
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code == "LITERAL_IN_REGISTER_LIST" for d in result.diagnostics)


def test_missing_return_type_descriptor_rejected():
    smali = """
    const-string v0, "Test"
    invoke-virtual {v0}, Landroid/widget/Toast;->show()
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code == "MISSING_RETURN_TYPE" for d in result.diagnostics)
    diag = next(d for d in result.diagnostics if d.error_code == "MISSING_RETURN_TYPE")
    assert "show()" in diag.line_content
    assert "missing a return type" in diag.message.lower()


def test_illegal_return_void_with_operands():
    smali = """
    const/4 v0, 0x1
    return-void v0
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code == "ILLEGAL_OPERAND" for d in result.diagnostics)


def test_out_of_range_registers_detected():
    smali = """
.method public static testRegisters()V
    .registers 2
    const-string v5, "Out of range register"
    return-void
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code == "OUT_OF_RANGE_REGISTER" for d in result.diagnostics)
    diag = next(d for d in result.diagnostics if d.error_code == "OUT_OF_RANGE_REGISTER")
    assert "v5" in diag.message


def test_invalid_register_names_rejected():
    smali = """
    const-string r0, "ARM register style invalid in Dalvik"
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code == "INVALID_REGISTER_NAME" for d in result.diagnostics)


def test_undefined_branch_target_label():
    smali = """
.method public static testBranch()V
    .registers 1
    goto :non_existent_label
    return-void
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code == "UNDEFINED_LABEL" for d in result.diagnostics)


def test_nested_method_declaration_rejected():
    smali = """
.method public static outer()V
    .registers 1
    .method public static inner()V
        return-void
    .end method
    return-void
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code == "NESTED_METHOD" for d in result.diagnostics)


def test_empty_or_whitespace_input():
    assert SmaliBytecodeValidator.validate("").is_valid is True
    assert SmaliBytecodeValidator.validate("   \n\n  # comment only\n").is_valid is True
