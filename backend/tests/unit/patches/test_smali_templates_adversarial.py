"""Adversarial tests for Smali intent and method templates."""

import pytest
from noir.patches.smali_templates import (
    generate_permission_check_smali,
    wrap_smali_method,
    generate_permission_request_smali,
    SmaliTemplateError,
)
from noir.patches.smali_validator import SmaliBytecodeValidator


def test_permission_check_jumps_on_granted_zero():
    """PackageManager.PERMISSION_GRANTED is 0. Branching to granted_label requires if-eqz."""
    smali = generate_permission_check_smali(
        "android.permission.CAMERA",
        granted_label=":perm_granted",
        validate=False,
    )
    # Check that it branches to :perm_granted when result is 0 (GRANTED)
    assert "if-eqz" in smali
    assert "if-nez" not in smali


def test_wrap_smali_method_primitive_return_type():
    """Methods returning boolean (Z) or int (I) must return v0, not return-object v0."""
    code = wrap_smali_method(
        "const/4 v0, 0x1",
        "hasPermission(Landroid/content/Context;)Z",
    )
    assert "return v0" in code
    assert "return-object" not in code

    val = SmaliBytecodeValidator.validate(code)
    assert val.is_valid is True


def test_wrap_smali_method_wide_return_type():
    """Methods returning long (J) or double (D) must return-wide v0."""
    code = wrap_smali_method(
        "const-wide v0, 0x1L",
        "getTimestamp()J",
        locals_count=2,
    )
    assert "return-wide v0" in code

    val = SmaliBytecodeValidator.validate(code)
    assert val.is_valid is True


def test_permission_request_more_than_7_permissions():
    """More than 7 permissions should not emit const/4 with overflow value."""
    perms = [f"android.permission.PERM_{i}" for i in range(10)]
    smali = generate_permission_request_smali(perms, validate=False)
    # 10 is 0xa which exceeds 4-bit signed max (7)
    # The array size must use const/16 or const
    assert "const/4 v0, 0xa" not in smali
    assert "const/16 v0, 0xa" in smali or "const v0, 0xa" in smali
