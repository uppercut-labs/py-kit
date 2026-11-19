"""Kill a process together with everything it started."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Union

IS_WINDOWS = os.name == "nt"

_POLL = 0.02

Target = Union["subprocess.Popen[Any]", int]


@dataclass(frozen=True)
class KillInfo:
    """What `kill_tree` did.

    method: "killpg" (POSIX process group), "taskkill" (Windows), "terminate" (leader only) or "none" when
        nothing was left to kill.
    already_exited: the tree was already gone when the call started.
    graceful: the soft signal was enough and no forced kill was needed.
    forced: SIGKILL (POSIX) or taskkill /F (Windows) was used.
    """

    method: str
    already_exited: bool = False
    graceful: bool = False
    forced: bool = False


def popen_group_kwargs(*, no_window: bool = False) -> dict[str, Any]:
    """Popen keyword arguments that put the child in its own session or process group.

    A child started this way can be killed together with its descendants by `kill_tree`.
    `no_window` adds CREATE_NO_WINDOW on Windows and is ignored elsewhere.
    """
    if IS_WINDOWS:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if no_window:
            flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def popen_group(argv: Any, *, no_window: bool = False, **kwargs: Any) -> "subprocess.Popen[Any]":
    """`subprocess.Popen` with `popen_group_kwargs` applied."""
    kwargs.update(popen_group_kwargs(no_window=no_window))
    return subprocess.Popen(argv, **kwargs)  # noqa: S603


def kill_tree(target: Target, *, grace: float = 5.0) -> KillInfo:
    """Stop a process and all of its descendants.

    POSIX: sends SIGTERM to the process group, waits up to `grace` seconds for the group to empty, then sends
    SIGKILL. The target must lead its own group (start it with `popen_group` or `start_new_session=True`).
    The calling process's own group is never signalled; a target in it raises ValueError.

    Windows: sends CTRL_BREAK_EVENT to a `Popen` started in a new process group, waits up to `grace` seconds,
    then runs `taskkill /F /T`. Descendants of a leader that exited during the grace period are not reached.

    A `Popen` target is reaped. An integer pid is only signalled. `grace=0` skips the soft step.
    """
    if grace < 0:
        raise ValueError("grace must be zero or positive")
    if IS_WINDOWS:
        return _kill_windows(target, grace)
    return _kill_posix(target, grace)


def _pid_of(target: Target) -> "tuple[int, subprocess.Popen[Any] | None]":
    if isinstance(target, subprocess.Popen):
        return target.pid, target
    return int(target), None


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Members we cannot signal (or zombies on some systems): nothing more we can do.
        return False
    return True


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _reap(proc: "subprocess.Popen[Any]") -> None:
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _kill_posix(target: Target, grace: float) -> KillInfo:
    pid, proc = _pid_of(target)
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        # The leader is already gone; its group id stays the same while any member lives.
        pgid = pid
    if pgid == os.getpgrp():
        raise ValueError(
            "refusing to kill the calling process's own process group; start the child with popen_group()"
        )
    if proc is not None:
        proc.poll()
    if not _group_exists(pgid):
        if proc is not None:
            _reap(proc)
        return KillInfo(method="none", already_exited=True)

    if grace > 0:
        _signal_group(pgid, signal.SIGTERM)
        end = time.monotonic() + grace
        while True:
            if proc is not None:
                proc.poll()
            if not _group_exists(pgid):
                if proc is not None:
                    _reap(proc)
                return KillInfo(method="killpg", graceful=True)
            if time.monotonic() >= end:
                break
            time.sleep(_POLL)
    _signal_group(pgid, signal.SIGKILL)
    if proc is not None:
        _reap(proc)
    end = time.monotonic() + 2.0
    while _group_exists(pgid) and time.monotonic() < end:
        time.sleep(_POLL)
    return KillInfo(method="killpg", forced=True)


def _taskkill(pid: int) -> bool:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        done = subprocess.run(  # noqa: S603
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _kill_windows(target: Target, grace: float) -> KillInfo:
    pid, proc = _pid_of(target)
    if proc is not None and proc.poll() is not None:
        return KillInfo(method="none", already_exited=True)
    if proc is not None and grace > 0:
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        except (OSError, ValueError, AttributeError):
            pass
        try:
            proc.wait(timeout=grace)
            _taskkill(pid)  # sweeps any descendants that are still reachable
            return KillInfo(method="taskkill", graceful=True)
        except subprocess.TimeoutExpired:
            pass
    if not _taskkill(pid) and proc is not None:
        try:
            proc.kill()
        except OSError:
            pass
    if proc is not None:
        _reap(proc)
    return KillInfo(method="taskkill", forced=True)
