"""Adversarial Round 3 tests for Dalvik Smali Bytecode Validator.

Focuses on:
1. Dalvik 038+ opcodes (invoke-polymorphic, invoke-custom, const-method-type, const-method-handle).
2. Unknown/hallucinated opcodes and unknown directives.
3. Multi-nested .annotation and .subannotation blocks with unclosed and unmatched validation.
4. Data payload blocks (.packed-switch, .sparse-switch, .array-data) with unclosed and unmatched checks.
5. Escaped closing quote detection in const-string literals.
6. Array type descriptor requirements for new-array and filled-new-array / filled-new-array/range.
7. Three-register opcode arity and wide register boundaries.
8. Insufficient registers vs method parameter count.
9. Literal bounds for rsub-int.
"""

import pytest
from noir.patches.smali_validator import SmaliBytecodeValidator


class TestSmaliValidatorR3Dalvik038:
    """Test Dalvik 038+ instructions."""

    def test_invoke_polymorphic_valid(self):
        smali = """
        .method public testPoly()V
            .registers 3
            invoke-polymorphic {v0, v1}, Ljava/lang/invoke/MethodHandle;->invoke([Ljava/lang/Object;)Ljava/lang/Object;, (Ljava/lang/String;)I
            move-result v0
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert res.is_valid, res.error_summary

    def test_invoke_polymorphic_invalid_callsite_return(self):
        smali = """
        .method public testPoly()V
            .registers 3
            invoke-polymorphic {v0, v1}, Ljava/lang/invoke/MethodHandle;->invoke([Ljava/lang/Object;)Ljava/lang/Object;, (Ljava/lang/String;)InvalidType;
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert not res.is_valid
        assert any(d.error_code == "INVALID_RETURN_TYPE" for d in res.diagnostics)

    def test_invoke_custom_valid(self):
        smali = """
        .method public testCustom()V
            .registers 2
            invoke-custom {v0}, Lcom/example/Bootstrap;->bsm()Ljava/lang/invoke/CallSite;, testMethod()V
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert res.is_valid, res.error_summary

    def test_invoke_custom_malformed_bsm(self):
        smali = """
        .method public testCustom()V
            .registers 2
            invoke-custom {v0}, Lcom/example/Bootstrap;no_arrow, testMethod()V
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert not res.is_valid
        assert any(d.error_code == "MALFORMED_METHOD_TARGET" for d in res.diagnostics)

    def test_const_method_type_valid(self):
        smali = """
        .method public testMethodType()V
            .registers 2
            const-method-type v0, (ILjava/lang/String;)V
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert res.is_valid, res.error_summary

    def test_const_method_type_invalid_proto(self):
        smali = """
        .method public testMethodType()V
            .registers 2
            const-method-type v0, not_a_proto
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert not res.is_valid
        assert any(d.error_code == "MALFORMED_METHOD_SIGNATURE" for d in res.diagnostics)

    def test_const_method_handle_valid(self):
        smali = """
        .method public testMethodHandle()V
            .registers 2
            const-method-handle v0, invoke-static, Ljava/lang/String;->valueOf(I)Ljava/lang/String;
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert res.is_valid, res.error_summary

    def test_const_method_handle_invalid_kind(self):
        smali = """
        .method public testMethodHandle()V
            .registers 2
            const-method-handle v0, invalid-kind, Ljava/lang/String;->valueOf(I)Ljava/lang/String;
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert not res.is_valid
        assert any(d.error_code == "ILLEGAL_OPERAND" for d in res.diagnostics)


class TestSmaliValidatorR3OpcodeAndDirectives:
    """Test unknown opcodes, unknown directives, and syntax."""

    def test_hallucinated_opcode_rejected(self):
        smali = """
        .method public test()V
            .registers 2
            mov v0, v1
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert not res.is_valid
        assert any(d.error_code == "UNKNOWN_OPCODE" for d in res.diagnostics)

    def test_unknown_directive_rejected(self):
        smali = """
        .method public test()V
            .registers 2
            .unknown_directive 123
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert not res.is_valid
        assert any(d.error_code == "UNKNOWN_DIRECTIVE" for d in res.diagnostics)

    def test_insufficient_registers_vs_params(self):
        smali = """
        .method public test(IIII)V
            .registers 3
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert not res.is_valid
        assert any(d.error_code == "INSUFFICIENT_REGISTERS" for d in res.diagnostics)


class TestSmaliValidatorR3NestedAnnotations:
    """Test multi-nested .annotation and .subannotation blocks."""

    def test_multi_nested_annotations_valid(self):
        smali = """
        .method public test()V
            .registers 1
            .annotation build Lcom/example/Outer;
                value = .subannotation Lcom/example/Inner;
                    name = "nested"
                    tag = .subannotation Lcom/example/Deep;
                        level = 3
                    .end subannotation
                .end subannotation
            .end annotation
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert res.is_valid, res.error_summary

    def test_unclosed_annotation_inside_method(self):
        smali = """
        .method public test()V
            .registers 1
            .annotation build Lcom/example/Outer;
                value = 1
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert not res.is_valid
        assert any(d.error_code == "UNCLOSED_ANNOTATION" for d in res.diagnostics)

    def test_unmatched_end_annotation(self):
        smali = """
        .method public test()V
            .registers 1
            .end annotation
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert not res.is_valid
        assert any(d.error_code == "UNMATCHED_END_ANNOTATION" for d in res.diagnostics)


class TestSmaliValidatorR3DataPayloads:
    """Test data payload directives (.packed-switch, .sparse-switch, .array-data)."""

    def test_unclosed_packed_switch(self):
        smali = """
        .method public test()V
            .registers 2
            packed-switch v0, :pswitch_data
            :pswitch_data
            .packed-switch 0x0
                :case_0
            return-void
            :case_0
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert not res.is_valid
        assert any(d.error_code == "UNCLOSED_DATA_PAYLOAD" for d in res.diagnostics)

    def test_unmatched_end_sparse_switch(self):
        smali = """
        .method public test()V
            .registers 1
            .end sparse-switch
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert not res.is_valid
        assert any(d.error_code == "UNMATCHED_END_DIRECTIVE" for d in res.diagnostics)

    def test_unclosed_array_data_at_eof(self):
        smali = """
        .array-data 4
            0x1 0x2
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert not res.is_valid
        assert any(d.error_code == "UNCLOSED_DATA_PAYLOAD" for d in res.diagnostics)


class TestSmaliValidatorR3StringsAndArrays:
    """Test string escaping, new-array, and filled-new-array."""

    def test_const_string_escaped_closing_quote_fails(self):
        # A string ending in \" is unclosed because the quote is escaped
        smali = r'const-string v0, "unclosed\"'
        res = SmaliBytecodeValidator.validate(smali)
        assert not res.is_valid
        assert any(d.error_code == "ILLEGAL_OPERAND" for d in res.diagnostics)

    def test_const_string_escaped_backslash_succeeds(self):
        # A string ending in \\" is closed because the backslash itself is escaped
        smali = r'const-string v0, "closed\\"'
        res = SmaliBytecodeValidator.validate(smali)
        assert res.is_valid, res.error_summary

    def test_new_array_requires_array_descriptor(self):
        smali = """
        .method public test()V
            .registers 3
            new-array v0, v1, Ljava/lang/String;
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert not res.is_valid
        assert any(d.error_code == "INVALID_TYPE_DESCRIPTOR" for d in res.diagnostics)

    def test_new_array_valid_primitive_and_object_arrays(self):
        smali = """
        .method public test()V
            .registers 3
            new-array v0, v1, [I
            new-array v0, v1, [Ljava/lang/String;
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert res.is_valid, res.error_summary

    def test_filled_new_array_range_valid(self):
        smali = """
        .method public test()V
            .registers 5
            filled-new-array/range {v0 .. v3}, [I
            move-result-object v4
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert res.is_valid, res.error_summary

    def test_filled_new_array_non_array_descriptor_fails(self):
        smali = """
        .method public test()V
            .registers 3
            filled-new-array {v0, v1}, I
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert not res.is_valid
        assert any(d.error_code == "INVALID_TYPE_DESCRIPTOR" for d in res.diagnostics)


class TestSmaliValidatorR3ThreeRegOpcodes:
    """Test 3-register opcodes and rsub-int."""

    def test_add_int_arity_mismatch(self):
        smali = """
        .method public test()V
            .registers 3
            add-int v0, v1
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert not res.is_valid
        assert any(d.error_code == "ILLEGAL_OPERAND" for d in res.diagnostics)

    def test_add_long_wide_register_overflow(self):
        # add-long v0, v1, v2 needs (v0, v1), (v1, v2), (v2, v3).
        # With .registers 3, v3 is out of range!
        smali = """
        .method public test()V
            .registers 3
            add-long v0, v1, v2
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert not res.is_valid
        assert any(d.error_code == "OUT_OF_RANGE_REGISTER" for d in res.diagnostics)

    def test_rsub_int_literal_overflow(self):
        smali = """
        .method public test()V
            .registers 3
            rsub-int v0, v1, 40000
            return-void
        .end method
        """
        res = SmaliBytecodeValidator.validate(smali)
        assert not res.is_valid
        assert any(d.error_code == "LITERAL_OUT_OF_RANGE" for d in res.diagnostics)
