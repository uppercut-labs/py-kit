"""Run one command with a timeout, an output cap and cancellation."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ._kill import KillInfo, kill_tree, popen_group_kwargs

OnOutput = Callable[[str, bytes], None]

_POLL = 0.02
_READ = 65536


class CommandError(RuntimeError):
    """Raised by `run(check=True)` and `RunResult.check_returncode()`. The result is in `.result`."""

    def __init__(self, message: str, result: "RunResult") -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class RunResult:
    """Outcome of `run`.

    stop_reason is one of "exited", "timeout", "cancelled", "output-limit" or "spawn-failed".
    stdout and stderr hold at most `max_output` bytes from the start of each stream. The tails hold the last
    `tail_bytes` bytes of each stream, even when the stream was longer than `max_output`.
    exit_code is None when the command never started; after a kill on POSIX it is the negative signal number.
    """

    argv: tuple[str, ...]
    exit_code: int | None
    stop_reason: str
    duration: float
    stdout: bytes
    stderr: bytes
    stdout_tail: bytes
    stderr_tail: bytes
    truncated: bool
    pid: int | None
    kill: KillInfo | None = None
    error: str | None = None

    @property
    def timed_out(self) -> bool:
        return self.stop_reason == "timeout"

    @property
    def cancelled(self) -> bool:
        return self.stop_reason == "cancelled"

    @property
    def ok(self) -> bool:
        """The command ran to the end by itself and exited with code 0."""
        return self.stop_reason == "exited" and self.exit_code == 0

    @property
    def stdout_text(self) -> str:
        return self.stdout.decode("utf-8", "replace")

    @property
    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8", "replace")

    def error_tail(self, limit: int = 2000) -> str:
        """The last `limit` characters of stderr, or of stdout when stderr is empty."""
        data = self.stderr_tail or self.stdout_tail
        return data.decode("utf-8", "replace")[-limit:]

    def check_returncode(self, *, tail: int = 2000) -> "RunResult":
        """Return self when `ok`, otherwise raise CommandError with the tail of the output."""
        if self.ok:
            return self
        name = self.argv[0] if self.argv else "command"
        if self.stop_reason == "exited":
            what = "exited with code %s" % self.exit_code
        elif self.stop_reason == "spawn-failed":
            what = "could not start (%s)" % (self.error or "unknown error")
        else:
            what = "stopped: %s" % self.stop_reason
        text = self.error_tail(tail).strip()
        message = "%s %s" % (name, what) + (":\n" + text if text else "")
        raise CommandError(message, self)


class _Stream:
    """Reads one pipe on a thread, keeping the head up to a cap and a rolling tail."""

    def __init__(
        self,
        name: str,
        pipe: Any,
        max_bytes: int | None,
        tail_bytes: int,
        stop_on_limit: bool,
        limit_event: threading.Event,
        on_output: OnOutput | None,
    ) -> None:
        self.name = name
        self.head = bytearray()
        self.tail = bytearray()
        self.truncated = False
        self._pipe = pipe
        self._max = max_bytes
        self._tail_bytes = tail_bytes
        self._stop_on_limit = stop_on_limit
        self._limit_event = limit_event
        self._on_output = on_output
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self) -> None:
        try:
            while True:
                chunk = self._pipe.read(_READ)
                if not chunk:
                    return
                self._feed(chunk)
        except (OSError, ValueError):
            return

    def _feed(self, chunk: bytes) -> None:
        if self._max is None:
            self.head += chunk
        else:
            room = self._max - len(self.head)
            if room > 0:
                self.head += chunk[:room]
            if len(chunk) > max(room, 0):
                self.truncated = True
                if self._stop_on_limit:
                    self._limit_event.set()
        if self._tail_bytes > 0:
            self.tail += chunk
            if len(self.tail) > 2 * self._tail_bytes:
                del self.tail[: -self._tail_bytes]
        if self._on_output is not None:
            try:
                self._on_output(self.name, bytes(chunk))
            except Exception:  # a broken callback must not stop the capture
                pass

    def final_tail(self) -> bytes:
        return bytes(self.tail[-self._tail_bytes :]) if self._tail_bytes > 0 else b""


def _feed_stdin(pipe: Any, data: bytes) -> None:
    try:
        pipe.write(data)
    except (OSError, ValueError):
        pass
    finally:
        try:
            pipe.close()
        except (OSError, ValueError):
            pass


def _stop_leader_only(proc: "subprocess.Popen[Any]", grace: float) -> KillInfo:
    if proc.poll() is not None:
        return KillInfo(method="none", already_exited=True)
    proc.terminate()
    try:
        proc.wait(timeout=grace)
        return KillInfo(method="terminate", graceful=True)
    except subprocess.TimeoutExpired:
        proc.kill()
        return KillInfo(method="terminate", forced=True)


def run(
    argv: "Sequence[str | os.PathLike[str]]",
    *,
    cwd: "str | os.PathLike[str] | None" = None,
    env: Mapping[str, str] | None = None,
    input: bytes | None = None,
    timeout: float | None = None,
    max_output: int | None = 10_000_000,
    tail_bytes: int = 8192,
    stop_on_output_limit: bool = True,
    grace: float = 5.0,
    cancel: threading.Event | None = None,
    on_output: OnOutput | None = None,
    kill_process_tree: bool = True,
    drain_timeout: float = 1.0,
    no_window: bool = False,
    check: bool = False,
) -> RunResult:
    """Run `argv` (no shell) and return a `RunResult`. Never raises for a failing command unless `check` is set.

    The child starts in its own session (POSIX) or process group (Windows). It is stopped, together with its
    descendants, when `timeout` seconds pass, when `cancel` is set, or when one output stream exceeds
    `max_output` bytes (unless `stop_on_output_limit` is False, which keeps draining and discards the excess).
    Stopping sends a soft signal, waits `grace` seconds, then forces the kill.

    stdin is closed unless `input` is given. If the command exits but a descendant keeps its pipes open, the
    tree is killed after `drain_timeout` seconds so the call returns.
    """
    args = tuple(os.fspath(a) for a in argv)
    if not args:
        raise ValueError("argv must not be empty")
    if timeout is not None and timeout <= 0:
        raise ValueError("timeout must be positive")
    if max_output is not None and max_output <= 0:
        raise ValueError("max_output must be positive")
    if tail_bytes < 0:
        raise ValueError("tail_bytes must not be negative")
    if grace < 0:
        raise ValueError("grace must be zero or positive")

    start = time.monotonic()
    try:
        proc = subprocess.Popen(  # noqa: S603
            list(args),
            cwd=os.fspath(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            shell=False,
            **popen_group_kwargs(no_window=no_window),
        )
    except (OSError, ValueError) as exc:
        failed = RunResult(
            argv=args,
            exit_code=None,
            stop_reason="spawn-failed",
            duration=time.monotonic() - start,
            stdout=b"",
            stderr=b"",
            stdout_tail=b"",
            stderr_tail=b"",
            truncated=False,
            pid=None,
            error=str(exc),
        )
        return failed.check_returncode() if check else failed

    limit_event = threading.Event()
    streams = [
        _Stream(name, pipe, max_output, tail_bytes, stop_on_output_limit, limit_event, on_output)
        for name, pipe in (("stdout", proc.stdout), ("stderr", proc.stderr))
    ]
    if input is not None:
        threading.Thread(target=_feed_stdin, args=(proc.stdin, input), daemon=True).start()

    deadline = start + timeout if timeout is not None else None
    reason = "exited"
    while proc.poll() is None:
        if cancel is not None and cancel.is_set():
            reason = "cancelled"
        elif limit_event.is_set():
            reason = "output-limit"
        elif deadline is not None and time.monotonic() >= deadline:
            reason = "timeout"
        if reason != "exited":
            break
        try:
            proc.wait(timeout=_POLL)
        except subprocess.TimeoutExpired:
            pass

    kill: KillInfo | None = None
    if reason != "exited":
        kill = kill_tree(proc, grace=grace) if kill_process_tree else _stop_leader_only(proc, grace)
    proc.wait()

    for stream in streams:
        stream.thread.join(drain_timeout)
    if any(s.thread.is_alive() for s in streams):
        # A descendant still holds the pipes open.
        if kill_process_tree:
            late = kill_tree(proc, grace=0)
            kill = kill or late
        for stream in streams:
            stream.thread.join(1.0)
        for pipe in (proc.stdout, proc.stderr):
            try:
                pipe.close()
            except (OSError, ValueError):
                pass

    result = RunResult(
        argv=args,
        exit_code=proc.returncode,
        stop_reason=reason,
        duration=time.monotonic() - start,
        stdout=bytes(streams[0].head),
        stderr=bytes(streams[1].head),
        stdout_tail=streams[0].final_tail(),
        stderr_tail=streams[1].final_tail(),
        truncated=any(s.truncated for s in streams),
        pid=proc.pid,
        kill=kill,
    )
    return result.check_returncode() if check else result
