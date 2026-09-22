"""Adversarial Round 3 tests for Smali Templates.

Focuses on:
1. Negative start_reg and invalid register names.
2. Request code 32-bit bounds validation.
3. wrap_smali_method auto-scaling locals_count to prevent register starvation.
4. Notification permission template generation and validation.
"""

import pytest

from noir.patches.smali_templates import (
    generate_notification_permission_smali,
    generate_permission_check_smali,
    generate_permission_request_smali,
    generate_toast_smali,
    generate_url_redirect_smali,
    wrap_smali_method,
)
from noir.patches.smali_validator import SmaliBytecodeValidator


class TestSmaliTemplatesR3Validation:
    """Test defensive parameter validation in templates."""

    def test_negative_start_reg_rejected(self):
        with pytest.raises(ValueError, match="start_reg must be non-negative"):
            generate_url_redirect_smali("https://example.com", start_reg=-1)

        with pytest.raises(ValueError, match="start_reg must be non-negative"):
            generate_toast_smali("Hello", start_reg=-1)

        with pytest.raises(ValueError, match="start_reg must be non-negative"):
            generate_permission_check_smali("android.permission.CAMERA", start_reg=-1)

        with pytest.raises(ValueError, match="start_reg must be non-negative"):
            generate_permission_request_smali(["android.permission.CAMERA"], start_reg=-1)

        with pytest.raises(ValueError, match="start_reg must be non-negative"):
            generate_notification_permission_smali(start_reg=-1)

    def test_invalid_register_name_rejected(self):
        with pytest.raises(ValueError, match="Invalid register identifier"):
            generate_toast_smali("Hello", context_reg="r0")

        with pytest.raises(ValueError, match="Invalid register identifier"):
            generate_permission_request_smali(["android.permission.CAMERA"], activity_reg="a0")

    def test_request_code_out_of_range_rejected(self):
        with pytest.raises(ValueError, match="out of valid 32-bit integer range"):
            generate_permission_request_smali(
                ["android.permission.CAMERA"], request_code=0x1_0000_0000
            )

        with pytest.raises(ValueError, match="out of valid 32-bit integer range"):
            generate_notification_permission_smali(request_code=-3000000000)

    def test_wrap_smali_method_auto_scales_locals_for_high_registers(self):
        # instructions use v5, but locals_count=2 requested.
        # wrap_smali_method should auto-scale .locals to at least 6 so v5 is valid.
        insns = """
        const/4 v0, 0x1
        const/4 v5, 0x2
        return-void
        """
        code = wrap_smali_method(insns, "testMethod()V", locals_count=2)
        assert ".locals 6" in code
        res = SmaliBytecodeValidator.validate(code)
        assert res.is_valid, res.error_summary

    def test_wrap_smali_method_auto_scales_locals_for_wide_return(self):
        # return-wide v0 requires pair (v0, v1) -> at least 2 locals.
        # If locals_count=1 passed, auto-scale to 2.
        insns = """
        const-wide/16 v0, 0x42
        return-wide v0
        """
        code = wrap_smali_method(insns, "getLong()J", locals_count=1)
        assert ".locals 2" in code
        res = SmaliBytecodeValidator.validate(code)
        assert res.is_valid, res.error_summary

    def test_notification_permission_template_valid_and_assembled(self):
        code = generate_notification_permission_smali(
            activity_reg="p0",
            start_reg=1,
            request_code=2024,
            use_compat=True,
            validate=True,
        )
        assert "android.permission.POST_NOTIFICATIONS" in code
        assert "ActivityCompat;->requestPermissions" in code
