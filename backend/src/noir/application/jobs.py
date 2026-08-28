"""Persistent local task queue and cooperative cancellation of real tool processes."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import UTC, datetime

from noir.domain.enums import EventSeverity, JobState, WorkflowStage
from noir.domain.models import AuditEvent, JobInfo
from noir.infrastructure.database.repositories import EventRepository, JobRepository
from noir.infrastructure.processes.runner import CANCEL_CHECK, OUTPUT_SINK

TERMINAL = {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED, JobState.INTERRUPTED}


@contextmanager
def job_runtime(job_id):
    previous = CANCEL_CHECK.get()
    previous_sink = OUTPUT_SINK.get()

    def cancelled():
        job = JobRepository().get(job_id)
        return bool(
            (previous and previous())
            or job
            and (job.result_data.get("cancel_requested") or job.state == JobState.CANCELLED)
        )

    def output(line):
        if previous_sink:
            previous_sink(line)
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


class TaskQueue:
    """Single local worker. Queued tasks survive restart; interrupted tasks are not replayed."""

    def __init__(self, config):
        self.config = config
        self.stop_event = threading.Event()
        self.thread = None

    def submit(self, operation, project_id, payload, idempotency_key=None):
        repo = JobRepository()
        tasks = repo.list_all()
        if idempotency_key:
            for task in tasks:
                if task.result_data.get("idempotency_key") == idempotency_key:
                    if task.project_id != project_id or task.result_data.get("payload") != payload:
                        raise ValueError("Idempotency key reused with different request")
                    return task
        if sum(task.state == JobState.QUEUED for task in tasks) >= 32:
            raise ValueError("Job queue is full")
        stage = (
            WorkflowStage.VALIDATING_INPUT if operation == "import" else WorkflowStage.REBUILDING
        )
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
        for job in JobRepository().list_all():
            if job.result_data.get("operation") and job.state == JobState.RUNNING:
                job.state = JobState.INTERRUPTED
                job.error_message = (
                    "Worker stopped before completing this task; inspect before retrying"
                )
                JobRepository().update(job)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        for job in JobRepository().list_all():
            if job.result_data.get("operation") and job.state == JobState.RUNNING:
                JobRepository().request_cancel(job.job_id)
        if self.thread:
            self.thread.join(timeout=10)
        if hasattr(self, "lock_handle"):
            self.lock_handle.close()

    def _loop(self):
        while not self.stop_event.is_set():
            jobs = [
                j
                for j in reversed(JobRepository().list_all())
                if j.state == JobState.QUEUED and j.result_data.get("operation")
            ]
            if not jobs:
                self.stop_event.wait(0.2)
                continue
            self.execute(jobs[0])

    def execute(self, job):
        repo = JobRepository()
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
                    )
                elif job.result_data["operation"] == "build":
                    from noir.application.build_service import BuildService
                    from noir.infrastructure.database.repositories import ProjectRepository

                    project = ProjectRepository().get(job.project_id)
                    if not project or project.workspace_revision != payload["revision"]:
                        raise ValueError("Queued build references a stale revision")
                    result = BuildService(self.config).build(job.project_id).model_dump(mode="json")
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
