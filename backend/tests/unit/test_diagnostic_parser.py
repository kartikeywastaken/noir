"""Unit tests for structured compiler diagnostic parser (AAPT2 and Apktool)."""

from __future__ import annotations

from noir.infrastructure.tools.diagnostic_parser import (
    parse_build_failure,
    parse_diagnostics,
    parse_single_diagnostic,
)


def test_parse_aapt2_column_range() -> None:
    line = (
        "AndroidManifest.xml:14:5-22: AAPT: error: "
        'element <activity> is missing "android:name" attribute.'
    )
    diag = parse_single_diagnostic(line)
    assert diag is not None
    assert diag.file_path == "AndroidManifest.xml"
    assert diag.line_number == 14
    assert diag.column_number == 5
    assert diag.column_end == 22
    assert diag.severity == "error"
    assert diag.source_tool == "aapt2"
    assert diag.failure_shape == "missing_resource_attr"
    assert 'element <activity> is missing "android:name" attribute.' in diag.message


def test_parse_file_line_without_column() -> None:
    line = "res/values/strings.xml:10: error: duplicate value for resource 'string/app_name'"
    diag = parse_single_diagnostic(line)
    assert diag is not None
    assert diag.file_path == "res/values/strings.xml"
    assert diag.line_number == 10
    assert diag.column_number is None
    assert diag.severity == "error"
    assert diag.failure_shape == "duplicate_resource"
    assert "duplicate value for resource 'string/app_name'" in diag.message


def test_parse_apktool_prefix_with_warning_and_error() -> None:
    line = (
        "W: /workspace/res/values/strings.xml:10: error: "
        "duplicate value for resource 'string/app_name'"
    )
    diag = parse_single_diagnostic(line)
    assert diag is not None
    assert diag.file_path == "/workspace/res/values/strings.xml"
    assert diag.line_number == 10
    assert diag.severity == "error"
    assert diag.source_tool == "apktool"
    assert diag.failure_shape == "duplicate_resource"


def test_parse_line_paren_format() -> None:
    line = "E: Tag <activity> is missing attribute 'name' (line 42)"
    diag = parse_single_diagnostic(line)
    assert diag is not None
    assert diag.line_number == 42
    assert diag.severity == "error"
    assert diag.source_tool == "apktool"
    assert diag.failure_shape == "missing_resource_attr"
    assert "Tag <activity> is missing attribute 'name'" in diag.message


def test_parse_smali_bracket_format() -> None:
    line = (
        "smali/com/example/Main.smali[42,15] mismatched input 'const-string' expecting END_METHOD"
    )
    diag = parse_single_diagnostic(line)
    assert diag is not None
    assert diag.file_path == "smali/com/example/Main.smali"
    assert diag.line_number == 42
    assert diag.column_number == 15
    assert diag.severity == "error"
    assert diag.source_tool == "apktool"
    assert diag.failure_shape == "smali_syntax"
    assert "mismatched input" in diag.message


def test_classify_missing_return_descriptor() -> None:
    line = (
        "smali/com/example/Main.smali:30: error: missing return descriptor for "
        "invoke-virtual {v0}, Landroid/widget/Toast;->show()"
    )
    diag = parse_single_diagnostic(line)
    assert diag is not None
    assert diag.failure_shape == "missing_return_descriptor"


def test_classify_duplicate_attribute() -> None:
    line = "AndroidManifest.xml:18: error: duplicate attribute 'android:exported'"
    diag = parse_single_diagnostic(line)
    assert diag is not None
    assert diag.failure_shape == "duplicate_attribute"


def test_classify_undefined_resource() -> None:
    line = (
        "res/layout/main.xml:8:4: error: resource string/foo "
        "(aka com.example:string/foo) not found."
    )
    diag = parse_single_diagnostic(line)
    assert diag is not None
    assert diag.file_path == "res/layout/main.xml"
    assert diag.line_number == 8
    assert diag.column_number == 4
    assert diag.failure_shape == "undefined_resource"


def test_classify_invalid_xml_syntax() -> None:
    line = "res/values/strings.xml:5: error: XML or text declaration not at start of entity"
    diag = parse_single_diagnostic(line)
    assert diag is not None
    assert diag.failure_shape == "invalid_xml_syntax"


def test_classify_host_pollution() -> None:
    # 1. Java NIO Charset error
    diag1 = parse_single_diagnostic(
        "java.nio.charset.MalformedInputException: Illegal byte sequence"
    )
    assert diag1 is not None
    assert diag1.failure_shape == "host_pollution"

    # 2. AppleDouble sidecar file rejection by aapt2
    diag2 = parse_single_diagnostic(
        "W: /workspace/res/drawable/._icon.png: error: invalid file name."
    )
    assert diag2 is not None
    assert diag2.failure_shape == "host_pollution"

    # 3. .DS_Store file rejection
    diag3 = parse_single_diagnostic("error: invalid file name '.DS_Store'")
    assert diag3 is not None
    assert diag3.failure_shape == "host_pollution"


def test_parse_multi_line_diagnostics() -> None:
    multi_output = (
        "I: Using Apktool 2.9.3\n"
        "W: /workspace/res/values/strings.xml:10: "
        "error: duplicate value for resource 'string/app_name'\n"
        "AndroidManifest.xml:14:5-22: AAPT: error: "
        'element <activity> is missing "android:name" attribute.\n'
        "I: Built apk...\n"
    )
    diagnostics = parse_diagnostics(multi_output)
    assert len(diagnostics) == 2

    assert diagnostics[0].file_path == "/workspace/res/values/strings.xml"
    assert diagnostics[0].line_number == 10
    assert diagnostics[0].failure_shape == "duplicate_resource"

    assert diagnostics[1].file_path == "AndroidManifest.xml"
    assert diagnostics[1].line_number == 14
    assert diagnostics[1].column_number == 5
    assert diagnostics[1].column_end == 22
    assert diagnostics[1].failure_shape == "missing_resource_attr"


def test_parse_build_failure_summary() -> None:
    multi_output = (
        "I: Checking resource table...\n"
        "AndroidManifest.xml:14:5-22: AAPT: error: "
        'element <activity> is missing "android:name" attribute.\n'
    )
    summary = parse_build_failure(multi_output, stage="manifest_compile", default_tool="aapt2")
    assert summary["stage"] == "manifest_compile"
    assert summary["file"] == "AndroidManifest.xml"
    assert summary["line"] == 14
    assert summary["column"] == 5
    assert summary["column_end"] == 22
    assert summary["failure_shape"] == "missing_resource_attr"
    assert summary["severity"] == "error"
    assert summary["source_tool"] == "aapt2"
