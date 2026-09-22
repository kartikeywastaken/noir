"""Unit tests for Smali validator move-result* CFG branch target constraints."""

from noir.patches.smali_validator import SmaliBytecodeValidator


def test_valid_move_result_accepted():
    """A clean invoke followed immediately by move-result is valid."""
    smali = """
.method public static testCalculate()I
    .registers 2
    invoke-static {}, Lcom/example/Math;->compute()I
    move-result v0
    return v0
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is True
    assert len(result.diagnostics) == 0


def test_valid_move_result_object_accepted():
    """A clean invoke followed immediately by move-result-object is valid."""
    smali = """
.method public static testGetObject()Ljava/lang/String;
    .registers 2
    invoke-static {}, Lcom/example/Factory;->create()Ljava/lang/String;
    move-result-object v0
    return-object v0
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is True
    assert len(result.diagnostics) == 0


def test_valid_move_result_wide_accepted():
    """A clean invoke followed immediately by move-result-wide is valid."""
    smali = """
.method public static testGetWide()J
    .registers 3
    invoke-static {}, Lcom/example/Timer;->getTimestamp()J
    move-result-wide v0
    return-wide v0
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is True
    assert len(result.diagnostics) == 0


def test_if_branch_targeting_move_result_rejected():
    """A conditional branch targeting a label immediately preceding move-result is rejected."""
    smali = """
.method public static testBypass(I)I
    .registers 3
    if-nez v2, :cond_target
    invoke-static {}, Lcom/example/Calc;->run()I
    :cond_target
    move-result v0
    return v0
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code == "ILLEGAL_MOVE_RESULT_BRANCH_TARGET" for d in result.diagnostics)
    diag = next(
        d for d in result.diagnostics if d.error_code == "ILLEGAL_MOVE_RESULT_BRANCH_TARGET"
    )
    assert ":cond_target" in diag.message
    assert "move-result" in diag.message
    assert "Dalvik/ART verifier" in diag.message


def test_goto_targeting_move_result_rejected():
    """An unconditional goto targeting a label immediately preceding move-result is rejected."""
    smali = """
.method public static testGotoBypass()I
    .registers 2
    goto :mr_label
    invoke-static {}, Lcom/example/Calc;->run()I
    :mr_label
    move-result v0
    return v0
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code == "ILLEGAL_MOVE_RESULT_BRANCH_TARGET" for d in result.diagnostics)
    diag = next(
        d for d in result.diagnostics if d.error_code == "ILLEGAL_MOVE_RESULT_BRANCH_TARGET"
    )
    assert ":mr_label" in diag.message


def test_branch_targeting_move_result_object_rejected():
    """A branch targeting a label immediately preceding move-result-object is rejected."""
    smali = """
.method public static testObjectBypass(I)Ljava/lang/String;
    .registers 3
    if-eqz v2, :obj_target
    invoke-static {}, Lcom/example/Factory;->create()Ljava/lang/String;
    :obj_target
    move-result-object v0
    return-object v0
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code == "ILLEGAL_MOVE_RESULT_BRANCH_TARGET" for d in result.diagnostics)
    diag = next(
        d for d in result.diagnostics if d.error_code == "ILLEGAL_MOVE_RESULT_BRANCH_TARGET"
    )
    assert "move-result-object" in diag.message


def test_branch_targeting_move_result_wide_rejected():
    """A branch targeting a label immediately preceding move-result-wide is rejected."""
    smali = """
.method public static testWideBypass(I)J
    .registers 4
    if-ltz v3, :wide_target
    invoke-static {}, Lcom/example/Timer;->getTimestamp()J
    :wide_target
    move-result-wide v0
    return-wide v0
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code == "ILLEGAL_MOVE_RESULT_BRANCH_TARGET" for d in result.diagnostics)


def test_switch_target_targeting_move_result_rejected():
    """A packed-switch target targeting a label on move-result is rejected."""
    smali = """
.method public static testSwitch(I)I
    .registers 3
    packed-switch v2, :pswitch_data
    invoke-static {}, Lcom/example/Calc;->run()I
    :switch_target
    move-result v0
    return v0
    :pswitch_data
    .packed-switch 0x0
        :switch_target
    .end packed-switch
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code == "ILLEGAL_MOVE_RESULT_BRANCH_TARGET" for d in result.diagnostics)


def test_catch_handler_targeting_move_result_rejected():
    """An exception catch handler targeting a label on move-result is rejected."""
    smali = """
.method public static testCatch()I
    .registers 2
    .catch Ljava/lang/Exception; {:try_start .. :try_end} :catch_target
    :try_start
    invoke-static {}, Lcom/example/Calc;->run()I
    :try_end
    :catch_target
    move-result v0
    return v0
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code == "ILLEGAL_MOVE_RESULT_BRANCH_TARGET" for d in result.diagnostics)


def test_multiple_labels_before_move_result_all_tracked():
    """Multiple labels stacked immediately before move-result are all tracked."""
    smali = """
.method public static testStackedLabels(I)I
    .registers 3
    if-eqz v2, :label_first
    invoke-static {}, Lcom/example/Calc;->run()I
    :label_first
    :label_second
    move-result v0
    return v0
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code == "ILLEGAL_MOVE_RESULT_BRANCH_TARGET" for d in result.diagnostics)


def test_branch_targeting_label_after_move_result_accepted():
    """Branch targeting a label positioned AFTER move-result is valid."""
    smali = """
.method public static testValidFlow(I)I
    .registers 3
    if-eqz v2, :cond_after
    invoke-static {}, Lcom/example/Calc;->run()I
    move-result v0
    return v0
    :cond_after
    const/4 v0, 0x0
    return v0
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is True
    assert len(result.diagnostics) == 0


def test_move_result_without_invoke_rejected():
    """move-result without a preceding invoke instruction is rejected."""
    smali = """
.method public static testNoInvoke()I
    .registers 2
    const/4 v1, 0x1
    move-result v0
    return v0
.end method
"""
    result = SmaliBytecodeValidator.validate(smali)
    assert result.is_valid is False
    assert any(d.error_code == "ILLEGAL_MOVE_RESULT" for d in result.diagnostics)
