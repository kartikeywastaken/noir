from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest
from deploy.ec2.upgrade import (
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
