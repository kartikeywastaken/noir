import hashlib
import zipfile
from pathlib import Path

import pytest

from noir.domain.config import NoirConfig
from noir.domain.models import ProcessResult
from noir.infrastructure.apktool.adapter import ApkToolAdapter, overlay_preserved_entries


def test_overlay_restores_opaque_entries_and_keeps_injected_dex(tmp_path):
    original = tmp_path / "original.apk"
    rebuilt = tmp_path / "rebuilt.apk"
    opaque = {
        "classes.dex": b"original-dex",
        "classes3.dex": b"original-dex-three",
        "resources.arsc": b"compiled-resources",
        "assets/UCMobile/help/images/error.svg": b"odd-asset",
        "lib/arm64-v8a/libapp.so": b"native-library",
        "res/drawable/icon.png": b"compiled-resource",
        "kotlin/collections/collections.kotlin_builtins": b"unrecognized-root-entry",
    }
    with zipfile.ZipFile(original, "w") as archive:
        for name, data in opaque.items():
            archive.writestr(name, data)
        archive.writestr("AndroidManifest.xml", b"binary-original-manifest")
        archive.writestr("META-INF/OLD.SF", b"old-signature")

    with zipfile.ZipFile(rebuilt, "w") as archive:
        for name in opaque:
            archive.writestr(name, b"changed-or-placeholder")
        archive.writestr("classes4.dex", b"noir-runtime")
        archive.writestr("AndroidManifest.xml", b"new-manifest")
        archive.writestr("META-INF/REBUILT.RSA", b"stale-rebuilt-signature")

    overlay_preserved_entries(original, rebuilt)

    with zipfile.ZipFile(rebuilt) as archive:
        for name, expected in opaque.items():
            assert hashlib.sha256(archive.read(name)).digest() == hashlib.sha256(expected).digest()
        assert archive.read("classes4.dex") == b"noir-runtime"
        assert archive.read("AndroidManifest.xml") == b"new-manifest"
        assert "META-INF/OLD.SF" not in archive.namelist()
        assert "META-INF/REBUILT.RSA" not in archive.namelist()


def test_aapt2_failure_reports_manifest_file_line_and_stage():
    result = ProcessResult(
        command=["aapt2", "link"],
        exit_code=1,
        tool_name="aapt2",
        stderr="workspace/build/AndroidManifest.xml:42: error: unexpected element",
    )

    assert ApkToolAdapter.structured_failure(result) == {
        "stage": "manifest_compile",
        "file": "workspace/build/AndroidManifest.xml",
        "line": 42,
        "diagnostic": "error: unexpected element",
    }


def test_decode_retries_resource_copy_failure_with_keep_broken_res(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = NoirConfig(_env_file=None, data_dir=str(tmp_path), gemini_api_key="")
    adapter = ApkToolAdapter(config)
    apk = tmp_path / "input.apk"
    apk.write_bytes(b"PK")
    output = tmp_path / "decoded"
    calls: list[list[str]] = []

    def fake_run_tool(command: list[str], **_kwargs: object) -> ProcessResult:
        calls.append(command)
        output.mkdir()
        if len(calls) == 1:
            (output / "partial").write_text("incomplete", encoding="utf-8")
            return ProcessResult(
                command=command,
                exit_code=1,
                tool_name="apktool",
                stderr=("brut.directory.DirectoryException:\nError copying file:\nres/9E.xml"),
            )
        (output / "AndroidManifest.xml").write_text(
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android" />',
            encoding="utf-8",
        )
        return ProcessResult(command=command, exit_code=0, tool_name="apktool")

    monkeypatch.setattr(adapter, "_check_version", lambda: None)
    monkeypatch.setattr(adapter, "_build_base_command", lambda: ["apktool"])
    monkeypatch.setattr("noir.infrastructure.apktool.adapter.run_tool", fake_run_tool)

    result = adapter.decode(apk, output)

    assert result.exit_code == 0
    assert len(calls) == 2
    assert "--keep-broken-res" not in calls[0]
    assert calls[1][-1] == "--keep-broken-res"
    assert not (output / "partial").exists()
