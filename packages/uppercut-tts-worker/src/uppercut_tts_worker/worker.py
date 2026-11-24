"""Start a worker process and talk to it over the protocol."""

from __future__ import annotations

import atexit
import os
import subprocess
from typing import Any, Dict, Mapping, Optional, Sequence

from .priority import raise_tree_priority
from .protocol import read_message, write_message


class WorkerError(RuntimeError):
    """The worker did not become ready, or exited while a request was open."""


class WorkerProcess:
    """One resident worker: started once, sent many requests, closed at the end.

    `python` and `script` start it as `python script *args`. `env` is added to the environment. Its standard
    error goes to `log_path` (appended) when given. With `raise_priority` the process tree is lifted to above
    normal priority on Windows. A reply is waited for without a limit, as the worker may be loading a model.
    """

    def __init__(self, python: str, script: str, args: Sequence[str] = (), *, env: Optional[Mapping[str, str]] = None,
                 log_path: "str | os.PathLike[str] | None" = None, cwd: Optional[str] = None,
                 raise_priority: bool = False) -> None:
        self.argv = [python, script, *args]
        self.env = {**os.environ, **(env or {})}
        self.log_path = os.fspath(log_path) if log_path else None
        self.cwd = cwd
        self.raise_priority = raise_priority
        self.proc: Optional[subprocess.Popen] = None
        self.ready: Dict[str, Any] = {}
        self._log = None

    def start(self) -> "WorkerProcess":
        self._log = open(self.log_path, "a", encoding="utf-8") if self.log_path else subprocess.DEVNULL
        self.proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log,
                                     text=True, encoding="utf-8", env=self.env, cwd=self.cwd)
        if self.raise_priority:
            raise_tree_priority(self.proc.pid)
        atexit.register(self.close)
        return self

    def _see_log(self) -> str:
        return "; see %s" % self.log_path if self.log_path else ""

    def wait_ready(self) -> Dict[str, Any]:
        """Read the ready line; returns it (device, dtype and whatever else the worker reports)."""
        assert self.proc is not None and self.proc.stdout is not None, "start() first"
        message = read_message(self.proc.stdout)
        if not message or not message.get("ready"):
            self.close()
            raise WorkerError("worker did not become ready" + self._see_log())
        self.ready = message
        return message

    def request(self, request: Mapping[str, Any]) -> Dict[str, Any]:
        """Send one request and return the worker's response. Raises WorkerError if the worker exits first."""
        assert self.proc is not None and self.proc.stdin is not None and self.proc.stdout is not None, "start() first"
        try:
            write_message(self.proc.stdin, request)
        except (BrokenPipeError, OSError):
            raise WorkerError("worker exited mid-job" + self._see_log())
        response = read_message(self.proc.stdout)
        if response is None:
            raise WorkerError("worker exited mid-job" + self._see_log())
        return response

    def close(self, grace: float = 5.0) -> None:
        """Close the worker's input, wait `grace` seconds for it to exit, then terminate it."""
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if proc.stdout:
            proc.stdout.close()
        if self._log not in (None, subprocess.DEVNULL):
            self._log.close()
        atexit.unregister(self.close)

    def __enter__(self) -> "WorkerProcess":
        self.start()
        self.wait_ready()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
