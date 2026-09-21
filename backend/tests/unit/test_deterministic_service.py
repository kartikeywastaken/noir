from noir.application.deterministic_service import parse_operation_spec
from noir.patches.smali_validator import SmaliBytecodeValidator


def test_parse_all_guaranteed_operations_without_ai():
    spec = parse_operation_spec(
        "Rename the app to NOIR Demo, open https://example.com when the app launches "
        "and show a toast message saying 'Ready' on every tap"
    )

    assert spec is not None
    assert spec.app_name == "NOIR Demo"
    assert spec.launch_url == "https://example.com"
    assert spec.toast_message == "Ready"
    assert spec.intents == ["app_name", "launch_redirect", "every_tap_toast"]


def test_advanced_clause_does_not_enter_deterministic_profile():
    assert (
        parse_operation_spec("Rename the app to Demo and add camera permission")
        is None
    )


def test_enclosing_method_duplicate_label_is_rejected():
    existing = """.class public Lx/Test;
.super Ljava/lang/Object;
.method public run()V
    .locals 1
    :cond_0
    return-void
.end method
"""

    result = SmaliBytecodeValidator.validate(
        ":cond_0\nreturn-void",
        context_method="run()V",
        context_class="Lx/Test;",
        enclosing_file_content=existing,
    )

    assert not result.is_valid
    assert any(item.error_code == "DUPLICATE_LABEL" for item in result.diagnostics)
