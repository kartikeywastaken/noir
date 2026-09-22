from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest
from deploy.ec2.upgrade import (
    _health_is_release_ready,
    _preserve_apktool_jar,
    _replace_directory,
    _stage_archive,
    _validate_archive_members,
)


def _archive_member(name: str, *, kind: bytes = tarfile.REGTYPE) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.type = kind
    return member


@pytest.mark.parametrize(
    "name",
    [
        "../backend/escape.py",
        "/backend/escape.py",
        "frontend/index.ts",
        "backend/.venv/lib/python/site-packages/lxml/etree.so",
        "backend/src/noir/__pycache__/app.pyc",
        "tools/._Program.cs",
    ],
)
def test_validate_archive_rejects_unsafe_or_host_specific_members(name: str) -> None:
    with pytest.raises(RuntimeError, match="Unexpected deployment archive member"):
        _validate_archive_members([_archive_member(name)])


def test_validate_archive_rejects_links() -> None:
    _validate_archive_members([_archive_member("backend/src/noir/app.py")])
    with pytest.raises(RuntimeError, match="Unexpected deployment archive member"):
        _validate_archive_members(
            [_archive_member("backend/src/noir/linked.py", kind=tarfile.SYMTYPE)]
        )


def test_stage_archive_requires_complete_source_trees(tmp_path: Path) -> None:
    archive_path = tmp_path / "deployment.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        payload = b"backend"
        member = _archive_member("backend/README.md")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    with pytest.raises(RuntimeError, match="backend and tools"):
        _stage_archive(archive_path, tmp_path / "stage")


def test_replace_directory_removes_stale_files(tmp_path: Path) -> None:
    destination = tmp_path / "backend"
    destination.mkdir()
    (destination / "stale.py").write_text("old", encoding="utf-8")
    source = tmp_path / "staged-backend"
    source.mkdir()
    (source / "current.py").write_text("new", encoding="utf-8")

    _replace_directory(source, destination)

    assert not (destination / "stale.py").exists()
    assert (destination / "current.py").read_text(encoding="utf-8") == "new"
    assert not source.exists()


def test_preserve_apktool_jar_verifies_and_copies_runtime(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    (staging / "backend/deploy/ec2").mkdir(parents=True)
    (staging / "tools").mkdir()
    deployed_tools = tmp_path / "deployed-tools"
    deployed_tools.mkdir()
    payload = b"pinned apktool jar"
    (deployed_tools / "apktool_3.0.3.jar").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (staging / "backend/deploy/ec2/apktool.sha256").write_text(
        f"{digest}  apktool_3.0.3.jar\n", encoding="utf-8"
    )

    _preserve_apktool_jar(staging, deployed_tools)

    assert (staging / "tools/apktool_3.0.3.jar").read_bytes() == payload


def test_preserve_apktool_jar_rejects_checksum_mismatch(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    (staging / "backend/deploy/ec2").mkdir(parents=True)
    (staging / "tools").mkdir()
    deployed_tools = tmp_path / "deployed-tools"
    deployed_tools.mkdir()
    (deployed_tools / "apktool_3.0.3.jar").write_bytes(b"unexpected")
    (staging / "backend/deploy/ec2/apktool.sha256").write_text(
        f"{'0' * 64}  apktool_3.0.3.jar\n", encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        _preserve_apktool_jar(staging, deployed_tools)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {
                "status": "ok",
                "capabilities": {"import": True, "build": True, "ai": True},
            },
            True,
        ),
        (
            {
                "status": "ok",
                "capabilities": {"import": False, "build": True, "ai": True},
            },
            False,
        ),
        ({"status": "ok", "capabilities": {}}, False),
        ({"status": "error"}, False),
    ],
)
def test_health_release_gate_requires_full_server_capabilities(
    payload: object, expected: bool
) -> None:
    assert _health_is_release_ready(payload) is expected
