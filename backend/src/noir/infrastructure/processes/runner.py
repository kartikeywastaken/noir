"""Real subprocess execution with bounded capture, streaming and process-group cleanup."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from noir.domain.models import ProcessResult

MAX_OUTPUT_BYTES = 10 * 1024 * 1024
CANCEL_CHECK: ContextVar[Callable[[], bool] | None] = ContextVar("cancel_check", default=None)
OUTPUT_SINK: ContextVar[Callable[[str], None] | None] = ContextVar("output_sink", default=None)
_ENV_ALLOW = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TMP",
    "TEMP",
    "JAVA_HOME",
    "ANDROID_HOME",
    "ANDROID_SDK_ROOT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "LOCALAPPDATA",
    "APPDATA",
    "USERPROFILE",
}


def _sanitized_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key.upper() in _ENV_ALLOW}
    env.update(extra_env or {})
    return env


def _stop_group(proc: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
    else:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False
        )


def run_tool(
    args: list[str],
    *,
    cwd: str | Path | None = None,
    timeout: int = 300,
    env: dict[str, str] | None = None,
    tool_name: str = "",
    tool_version: str = "",
    capture_output: bool = True,
    input_data: str | None = None,
    allow_sensitive_env: bool = False,
) -> ProcessResult:
    """Run a tool, never a shell. Secrets belong in stdin or explicitly scoped env only."""
    if allow_sensitive_env:
        raise ValueError("Inheriting all host credentials is not supported")
    started = datetime.now(UTC)
    start = time.monotonic()
    command = [str(arg) for arg in args]
    cancel_check, sink = CANCEL_CHECK.get(), OUTPUT_SINK.get()
    buffers = [bytearray(), bytearray()]
    reader_errors: list[Exception] = []
    sensitive = [
        v for k, v in (env or {}).items() if any(s in k.upper() for s in ("PASS", "KEY", "TOKEN"))
    ]

    def redact(text: str) -> str:
        for secret in sensitive:
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text

    def drain(pipe, index):
        pending = ""
        pending_lines: list[str] = []
        pending_size = 0
        last_emit = time.monotonic()

        def flush(*, force: bool = False) -> None:
            nonlocal pending_lines, pending_size, last_emit
            if not sink or not pending_lines:
                return
            now = time.monotonic()
            if (
                not force
                and len(pending_lines) < 20
                and pending_size < 32_768
                and now - last_emit < 0.25
            ):
                return
            sink(redact("\n".join(pending_lines)))
            pending_lines = []
            pending_size = 0
            last_emit = now

        def queue_line(line: str) -> None:
            nonlocal pending_size
            line = line[:8192]
            pending_lines.append(line)
            pending_size += len(line.encode("utf-8", errors="replace"))
            flush()

        try:
            while chunk := pipe.read1(4096):
                buffers[index].extend(chunk)
                if len(buffers[index]) > MAX_OUTPUT_BYTES:
                    del buffers[index][:-MAX_OUTPUT_BYTES]
                pending += chunk.decode("utf-8", errors="replace")
                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    queue_line(line)
                if len(pending) > 8192:
                    queue_line(pending)
                    pending = ""
            if pending:
                queue_line(pending)
            flush(force=True)
        except Exception as exc:
            reader_errors.append(exc)
        finally:
            pipe.close()

    timed_out = cancelled = False
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd else None,
            env=_sanitized_env(env),
            stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        return ProcessResult(
            command=command,
            exit_code=-1,
            stderr=f"Command not found or could not start: {exc}",
            tool_name=tool_name,
            tool_version=tool_version,
        )
    readers = [
        threading.Thread(target=drain, args=(proc.stdout, 0), daemon=True),
        threading.Thread(target=drain, args=(proc.stderr, 1), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        if input_data is not None:
            try:
                proc.stdin.write(input_data.encode())
                proc.stdin.close()
            except BrokenPipeError:
                proc.stdin.close()
        while proc.poll() is None:
            cancelled = bool(cancel_check and cancel_check())
            timed_out = time.monotonic() - start >= timeout
            if cancelled or timed_out or reader_errors:
                _stop_group(proc)
                break
            time.sleep(0.05)
        proc.wait()
    except BaseException:
        _stop_group(proc)
        proc.wait()
        raise
    finally:
        for reader in readers:
            reader.join(timeout=0.5)
        if any(reader.is_alive() for reader in readers):
            _stop_group(proc)
            for reader in readers:
                reader.join(timeout=2)
    stdout, stderr = [redact(b.decode("utf-8", errors="replace")) for b in buffers]
    if timed_out:
        stderr += f"\nProcess timed out after {timeout} seconds"
    if cancelled:
        stderr += "\nProcess cancelled"
    if reader_errors:
        stderr += "\nProcess log delivery failed"
    return ProcessResult(
        command=command,
        exit_code=-1 if timed_out or cancelled or reader_errors else proc.returncode,
        stdout=stdout if capture_output else "",
        stderr=stderr,
        timed_out=timed_out,
        cancelled=cancelled,
        duration_seconds=round(time.monotonic() - start, 2),
        tool_name=tool_name,
        tool_version=tool_version,
        started_at=started,
        finished_at=datetime.now(UTC),
    )


def get_tool_version(tool_path: str, version_args: list[str] | None = None) -> str | None:
    result = run_tool([tool_path, *(version_args or ["--version"])], timeout=10)
    if result.exit_code == 0:
        return next(
            (line.strip() for line in (result.stdout + result.stderr).splitlines() if line.strip()),
            None,
        )
    return None
