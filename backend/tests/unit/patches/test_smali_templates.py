"""Unit tests for deterministic Smali intent templates."""

import pytest
from noir.patches.smali_templates import (
    generate_permission_check_smali,
    generate_permission_request_smali,
    generate_toast_smali,
    generate_url_redirect_smali,
    wrap_smali_method,
)
from noir.patches.smali_validator import SmaliBytecodeValidator


def test_url_redirect_intent_template_valid():
    smali = generate_url_redirect_smali("https://noir.internal/status", context_reg="p0")
    assert "https://noir.internal/status" in smali
    assert "Landroid/net/Uri;->parse(Ljava/lang/String;)Landroid/net/Uri;" in smali
    assert "Landroid/content/Intent;-><init>(Ljava/lang/String;Landroid/net/Uri;)V" in smali
    assert "startActivity(Landroid/content/Intent;)V" in smali

    # Must pass Dalvik pre-flight validation
    val = SmaliBytecodeValidator.validate(smali)
    assert val.is_valid is True, val.error_summary


def test_url_redirect_with_new_task_flag():
    smali = generate_url_redirect_smali(
        "https://example.com/login",
        context_reg="v4",
        start_reg=1,
        new_task=True,
    )
    assert "0x10000000" in smali
    assert "setFlags(I)Landroid/content/Intent;" in smali

    val = SmaliBytecodeValidator.validate(smali)
    assert val.is_valid is True, val.error_summary


def test_toast_notification_template_valid():
    smali = generate_toast_smali("Security Warning: Patched APK", context_reg="p0", duration=1)
    assert "Security Warning: Patched APK" in smali
    assert "Toast;->makeText(Landroid/content/Context;Ljava/lang/CharSequence;I)Landroid/widget/Toast;" in smali
    # Notice critical trailing 'V'
    assert "Toast;->show()V" in smali

    val = SmaliBytecodeValidator.validate(smali)
    assert val.is_valid is True, val.error_summary


def test_permission_check_template_valid():
    smali = generate_permission_check_smali(
        "android.permission.ACCESS_FINE_LOCATION",
        context_reg="p0",
        granted_label=":perm_ok",
    )
    assert "android.permission.ACCESS_FINE_LOCATION" in smali
    assert "checkSelfPermission(Ljava/lang/String;)I" in smali
    assert "if-eqz v0, :perm_ok" in smali

    # Validated with label defined
    val = SmaliBytecodeValidator.validate(smali + "\n:perm_ok\n")
    assert val.is_valid is True, val.error_summary


def test_permission_request_template_valid():
    perms = ["android.permission.CAMERA", "android.permission.RECORD_AUDIO"]
    smali = generate_permission_request_smali(perms, activity_reg="p0", request_code=2002)
    assert "new-array" in smali
    assert "android.permission.CAMERA" in smali
    assert "android.permission.RECORD_AUDIO" in smali
    assert "requestPermissions([Ljava/lang/String;I)V" in smali

    val = SmaliBytecodeValidator.validate(smali)
    assert val.is_valid is True, val.error_summary


def test_wrap_smali_method_valid():
    toast_insns = generate_toast_smali("Test inside method", context_reg="p0")
    method = wrap_smali_method(
        toast_insns,
        "showNotice(Landroid/content/Context;)V",
        access_flags="public static",
        locals_count=4,
    )
    assert method.startswith(".method public static showNotice(Landroid/content/Context;)V")
    assert ".locals 4" in method
    assert "return-void" in method
    assert method.endswith(".end method\n")

    val = SmaliBytecodeValidator.validate(method)
    assert val.is_valid is True, val.error_summary
