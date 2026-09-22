"""Adversarial unit tests for Smali Intent and Bytecode Templates (Round 2)."""

import pytest

from noir.patches.smali_templates import (
    generate_notification_permission_smali,
    generate_permission_check_smali,
    generate_permission_request_smali,
    generate_toast_smali,
    generate_url_redirect_smali,
)
from noir.patches.smali_validator import SmaliBytecodeValidator


def test_permission_request_large_request_code_uses_const():
    """Request codes exceeding 16-bit range (> 32767) automatically use 'const' without overflow."""
    code = generate_permission_request_smali(
        ["android.permission.CAMERA", "android.permission.RECORD_AUDIO"],
        request_code=65535,
    )
    assert "const v1, 0xffff" in code
    # Must pass validation
    res = SmaliBytecodeValidator.validate(code)
    assert res.is_valid, res.error_summary


def test_permission_request_compat_mode():
    """Permission request in compat mode uses ActivityCompat.requestPermissions."""
    code = generate_permission_request_smali(
        ["android.permission.ACCESS_FINE_LOCATION"],
        use_compat=True,
    )
    assert "ActivityCompat;->requestPermissions" in code
    res = SmaliBytecodeValidator.validate(code)
    assert res.is_valid, res.error_summary


def test_permission_check_compat_mode():
    """Permission check in compat mode uses ContextCompat.checkSelfPermission."""
    code = generate_permission_check_smali(
        "android.permission.ACCESS_FINE_LOCATION",
        use_compat=True,
    )
    assert "ContextCompat;->checkSelfPermission" in code
    snippet = code + "\n:cond_perm_granted\n"
    res = SmaliBytecodeValidator.validate(snippet)
    assert res.is_valid, res.error_summary


def test_permission_check_min_api_gated():
    """Permission check targeting min_api < 23 gates call with Build.VERSION.SDK_INT."""
    code = generate_permission_check_smali(
        "android.permission.READ_CONTACTS",
        min_api=21,
    )
    assert "Build$VERSION;->SDK_INT:I" in code
    snippet = code + "\n:cond_perm_granted\n"
    res = SmaliBytecodeValidator.validate(snippet)
    assert res.is_valid, res.error_summary


def test_notification_permission_template_android_13():
    """Android 13+ POST_NOTIFICATIONS template checks SDK_INT >= 33 and requests permission."""
    code = generate_notification_permission_smali()
    assert "Build$VERSION;->SDK_INT:I" in code
    assert "0x21" in code  # API 33
    assert "android.permission.POST_NOTIFICATIONS" in code
    snippet = code + "\n:cond_notify_granted\n"
    res = SmaliBytecodeValidator.validate(snippet)
    assert res.is_valid, res.error_summary


def test_toast_special_characters_escaping():
    """Toast notifications with quotes, newlines, and backslashes are properly escaped."""
    msg = 'Alert: "Root access denied"\\Failed\nRetry?'
    code = generate_toast_smali(msg)
    assert '\\"Root access denied\\"' in code
    assert "\\\\Failed" in code
    assert "\\nRetry?" in code
    res = SmaliBytecodeValidator.validate(code)
    assert res.is_valid, res.error_summary


def test_toast_invalid_duration_raises():
    """Toast with duration outside 0 or 1 raises ValueError."""
    with pytest.raises(ValueError, match="Toast duration must be 0"):
        generate_toast_smali("test", duration=5)


def test_url_redirect_empty_raises():
    """URL redirect with empty URL raises ValueError."""
    with pytest.raises(ValueError, match="URL cannot be empty"):
        generate_url_redirect_smali("")
