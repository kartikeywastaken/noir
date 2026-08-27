"""Cross-process project locks for the supported macOS/Linux local runner."""

from __future__ import annotations

import functools
import inspect
import threading
from contextlib import contextmanager

from noir.infrastructure.filesystem.workspace import ProjectWorkspace

_local = threading.local()


@contextmanager
def project_lock(config, project_id):
    import fcntl

    ProjectWorkspace(project_id, config)
    locks = config.projects_dir.parent / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    path = locks / f"{project_id}.lock"
    held = getattr(_local, "held", set())
    key = str(path.resolve())
    if key in held:
        yield
        return
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Project is busy in another operation") from exc
        _local.held = held | {key}
        try:
            yield
        finally:
            _local.held = held
            fcntl.flock(handle, fcntl.LOCK_UN)


def locked_project(method):
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        bound = inspect.signature(method).bind(self, *args, **kwargs).arguments
        project_id = bound.get("project_id")
        if project_id is None:
            obj = bound.get("plan") or bound.get("patch")
            project_id = obj.project_id
        with project_lock(self.config, project_id):
            return method(self, *args, **kwargs)

    return wrapped


def require_clean_workspace(config, project_id, *, allow_manual=False):
    from noir.infrastructure.database.repositories import (
        FileManifestRepository,
        ManualSessionRepository,
    )

    if not allow_manual and ManualSessionRepository().get_active(project_id):
        raise ValueError("An active manual-edit session must be recorded or cancelled first")
    ws = ProjectWorkspace(project_id, config)
    changes = ws.detect_changes(FileManifestRepository().get_latest(project_id) or [])
    if any(changes[name] for name in ("added", "modified", "deleted")):
        raise ValueError("Workspace contains unrecorded changes; use manual begin/record first")
