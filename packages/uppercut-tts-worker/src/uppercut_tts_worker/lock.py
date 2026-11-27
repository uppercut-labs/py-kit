"""One GPU job at a time on a machine: a lock file that holds the owner's process id."""

from __future__ import annotations

import atexit
import os
import sys
import time
from typing import Callable, Optional


class LockBusy(RuntimeError):
    """A live process holds the lock and `wait_s` ran out."""


def process_alive(pid: int) -> bool:
    """Is a process with this id running? On Windows a process that has exited but is still open elsewhere is dead."""
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # it exists; it belongs to someone else
    except OSError:
        return False
    return True


class GpuLock:
    """A lock file that names its owner's process id.

    `acquire()` creates the file exclusively. If it exists, the owner is read: a lock left by a process that
    is gone is removed and taken; a lock held by a live process is waited for, polling every `poll_s` seconds
    for up to `wait_s` seconds (0 means fail at once), and then `LockBusy` is raised. A live lock is never
    deleted. `release()` removes the file only while it still names this process, and runs at exit if the
    lock is still held. Use it as a context manager.
    """

    def __init__(self, path: "str | os.PathLike[str]", *, wait_s: float = 0.0, poll_s: float = 5.0,
                 on_wait: Optional[Callable[[str], None]] = None,
                 sleep: Callable[[float], None] = time.sleep, alive: Callable[[int], bool] = process_alive) -> None:
        self.path = os.fspath(path)
        self.wait_s = wait_s
        self.poll_s = poll_s
        self.on_wait = on_wait
        self._sleep = sleep
        self._alive = alive
        self.held = False

    def acquire(self) -> "GpuLock":
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        deadline = time.monotonic() + self.wait_s
        announced = False
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    with open(self.path, encoding="utf-8") as fh:
                        owner = fh.read().strip()
                except OSError:
                    self._sleep(0.05)  # released between our open and our read
                    continue
                if owner.isdigit() and not self._alive(int(owner)):
                    try:
                        os.remove(self.path)
                    except OSError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    waited = "" if not self.wait_s else " after %g s" % self.wait_s
                    raise LockBusy("GPU busy: pid %s still holds %s%s. Do not delete a live lock." % (owner or "?", self.path, waited))
                if not announced and self.on_wait:
                    self.on_wait("GPU busy: pid %s holds %s; waiting up to %g s" % (owner or "?", self.path, self.wait_s))
                announced = True
                self._sleep(self.poll_s)
                continue
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            self.held = True
            atexit.register(self.release)
            if announced and self.on_wait:
                self.on_wait("GPU lock acquired")
            return self

    def release(self) -> None:
        if not self.held:
            return
        self.held = False
        try:
            with open(self.path, encoding="utf-8") as fh:
                owner = fh.read().strip()
            if owner == str(os.getpid()):  # removed after the file is closed: Windows will not delete an open file
                os.remove(self.path)
        except OSError:
            pass
        atexit.unregister(self.release)

    def __enter__(self) -> "GpuLock":
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()
