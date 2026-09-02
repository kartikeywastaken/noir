"""Persistent local task queue and cooperative cancellation of real tool processes."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime

from noir.domain.enums import EventSeverity, JobState, WorkflowStage
from noir.domain.models import AuditEvent, JobInfo
from noir.infrastructure.database.repositories import EventRepository, JobRepository
from noir.infrastructure.processes.runner import CANCEL_CHECK, OUTPUT_SINK

TERMINAL = {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED, JobState.INTERRUPTED}
_CANCEL_EVENTS: dict[str, threading.Event] = {}
_CANCEL_EVENTS_LOCK = threading.Lock()


@contextmanager
def job_runtime(job_id):
    previous = CANCEL_CHECK.get()
    previous_sink = OUTPUT_SINK.get()
    cancel_event = threading.Event()
    last_db_check = 0.0
    db_cancelled = False
    with _CANCEL_EVENTS_LOCK:
        _CANCEL_EVENTS[job_id] = cancel_event

    def cancelled():
        nonlocal last_db_check, db_cancelled
        if (previous and previous()) or cancel_event.is_set() or db_cancelled:
            return True
        now = time.monotonic()
        if now - last_db_check >= 0.5:
            last_db_check = now
            job = JobRepository().get(job_id)
            db_cancelled = bool(
                job and (job.result_data.get("cancel_requested") or job.state == JobState.CANCELLED)
            )
        return db_cancelled

    def output(line):
        if previous_sink:
            previous_sink(line)
            return
        job = JobRepository().get(job_id)
        if job:
            EventRepository().create(
                AuditEvent(
                    project_id=job.project_id,
                    job_id=job_id,
                    stage=job.stage,
                    severity=EventSeverity.INFO,
                    message=line,
                )
            )

    cancel_token = CANCEL_CHECK.set(cancelled)
    output_token = OUTPUT_SINK.set(output)
    try:
        if cancelled():
            raise RuntimeError("Job cancelled before execution")
        yield
    finally:
        CANCEL_CHECK.reset(cancel_token)
        OUTPUT_SINK.reset(output_token)
        with _CANCEL_EVENTS_LOCK:
            _CANCEL_EVENTS.pop(job_id, None)


def notify_job_cancelled(job_id: str) -> None:
    """Wake an active local process immediately; the DB remains authoritative."""
    with _CANCEL_EVENTS_LOCK:
        event = _CANCEL_EVENTS.get(job_id)
        if event:
            event.set()


class TaskQueue:
    """Single local worker. Queued tasks survive restart; interrupted tasks are not replayed."""

    def __init__(self, config):
        self.config = config
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = None
        self.submit_lock = threading.Lock()

    def submit(self, operation, project_id, payload, idempotency_key=None):
        with self.submit_lock:
            job = self._submit(operation, project_id, payload, idempotency_key)
            self.wake_event.set()
            return job

    def _submit(self, operation, project_id, payload, idempotency_key=None):
        repo = JobRepository()
        if idempotency_key:
            task = repo.find_by_idempotency(idempotency_key)
            if task:
                if task.project_id != project_id or task.result_data.get("payload") != payload:
                    raise ValueError("Idempotency key reused with different request")
                return task
        if operation.startswith("workflow_") and repo.find_active_operation(project_id):
            raise ValueError("This APK already has an active operation. Resume it from History")
        if repo.count_queued_operations() >= 32:
            raise ValueError("Job queue is full")
        stage = (
            WorkflowStage.VALIDATING_INPUT if operation == "import" else WorkflowStage.REBUILDING
        )
        if operation == "workflow_prepare":
            stage = WorkflowStage.PLANNING
        job = JobInfo(
            project_id=project_id,
            stage=stage,
            result_data={
                "operation": operation,
                "payload": payload,
                "idempotency_key": idempotency_key,
            },
        )
        repo.create(job)
        return job

    def start(self):
        import fcntl

        lock_path = self.config.projects_dir.parent / "api-worker.lock"
        self.lock_handle = lock_path.open("a")
        try:
            fcntl.flock(self.lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.lock_handle.close()
            raise RuntimeError("Another NOIR API worker is already running") from exc
        for job in JobRepository().list_running_operations():
            job.state = JobState.INTERRUPTED
            job.error_message = (
                "Worker stopped before completing this task; inspect before retrying"
            )
            JobRepository().update(job)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.wake_event.set()
        for job in JobRepository().list_running_operations():
            self.cancel(job.job_id)
        if self.thread:
            self.thread.join(timeout=10)
        if hasattr(self, "lock_handle"):
            self.lock_handle.close()

    def _loop(self):
        while not self.stop_event.is_set():
            job = JobRepository().claim_next_queued()
            if not job:
                self.wake_event.wait(1.0)
                self.wake_event.clear()
                continue
            self.execute(job)

    def cancel(self, job_id: str):
        job = JobRepository().request_cancel(job_id)
        notify_job_cancelled(job_id)
        self.wake_event.set()
        return job

    def execute(self, job):
        repo = JobRepository()
        if job.state != JobState.RUNNING:
            job.state = JobState.RUNNING
            job.started_at = datetime.now(UTC)
            repo.update(job)
        try:
            with job_runtime(job.job_id):
                payload = job.result_data["payload"]
                if job.result_data["operation"] == "import":
                    from noir.application.import_service import ImportService

                    result = ImportService(self.config).import_apk(
                        payload["path"],
                        authorized=True,
                        project_id=job.project_id,
                        original_filename=payload.get("original_filename"),
                        job=job,
                        input_sha256=payload.get("sha256"),
                        input_size=payload.get("size"),
                        move_input=payload.get("move_input", False),
                    )
                elif job.result_data["operation"] == "build":
                    from noir.application.build_service import BuildService
                    from noir.infrastructure.database.repositories import ProjectRepository

                    project = ProjectRepository().get(job.project_id)
                    if not project or project.workspace_revision != payload["revision"]:
                        raise ValueError("Queued build references a stale revision")
                    result = (
                        BuildService(self.config)
                        .build(job.project_id, job=job)
                        .model_dump(mode="json")
                    )
                    if not result["success"]:
                        raise ValueError(result.get("error_message") or "APK rebuild failed")
                elif job.result_data["operation"] in {"workflow_prepare", "workflow_finish"}:
                    from noir.application.workflow_service import finish, prepare

                    action = (
                        prepare if job.result_data["operation"] == "workflow_prepare" else finish
                    )
                    result = action(self.config, job)
                else:
                    raise ValueError("Unsupported queued operation")
            job.result_data["result"] = result
            job.state = JobState.SUCCEEDED
        except Exception as exc:
            job.state = JobState.FAILED
            job.error_message = str(exc)
        finally:
            job.finished_at = datetime.now(UTC)
            repo.update(job)
            if job.result_data.get("operation") == "import":
                from pathlib import Path

                path = Path(job.result_data["payload"]["path"])
                uploads = (self.config.projects_dir.parent / "uploads").resolve()
                if path.resolve().is_relative_to(uploads):
                    path.unlink(missing_ok=True)
