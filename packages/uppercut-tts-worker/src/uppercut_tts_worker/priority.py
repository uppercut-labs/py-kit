"""Windows only: lift a process tree out of the low-power class that processes started over ssh get."""

from __future__ import annotations

import os
import time
from typing import List


def raise_tree_priority(pid: int, *, attempts: int = 20, pause: float = 0.1) -> List[int]:
    """Set the priority class of `pid` and its descendants to ABOVE_NORMAL; returns the ids it reached.

    Windows runs a process started from an ssh session at low clocks (EcoQoS); a higher class lifts that. A
    virtual environment's `python.exe` is a launcher whose child is the real interpreter and starts at the
    normal class whatever the launcher got, so the tree is searched up to `attempts` times, `pause` seconds
    apart, until the child exists. Does nothing and returns [] on other systems.
    """
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    k = ctypes.windll.kernel32  # type: ignore[attr-defined]

    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260)]

    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.Process32First.argtypes = k.Process32Next.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry)]
    k.OpenProcess.restype = ctypes.c_void_p
    k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    tree: List[int] = [pid]
    for _ in range(max(1, attempts)):
        snapshot, entry, children = k.CreateToolhelp32Snapshot(2, 0), ProcessEntry(), {}
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        ok = k.Process32First(snapshot, ctypes.byref(entry))
        while ok:
            children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
            ok = k.Process32Next(snapshot, ctypes.byref(entry))
        k.CloseHandle(snapshot)
        tree, todo = [], [pid]
        while todo:
            current = todo.pop()
            tree.append(current)
            todo += children.get(current, [])
        if len(tree) > 1:
            break
        time.sleep(pause)
    reached = []
    for member in tree:
        handle = k.OpenProcess(0x0200, False, member)  # PROCESS_SET_INFORMATION
        if handle:
            k.SetPriorityClass(handle, 0x8000)  # ABOVE_NORMAL_PRIORITY_CLASS
            k.CloseHandle(handle)
            reached.append(member)
    return reached
