"""Allocate one run directory with an ownership marker, and remove exactly that directory."""

from __future__ import annotations

import os
import re
import shutil
import stat
import sys
import uuid
from collections.abc import Iterable
from pathlib import Path

DEFAULT_MARKER = ".scratch-run"
RUN_NAME = re.compile(r"run-[0-9a-f]{32}")
_MARKER_NAME = re.compile(r"[A-Za-z0-9._-]+")


def check_marker(marker: str) -> str:
    """Return ``marker`` if it is a plain file name that cannot collide with a run directory."""
    if (not isinstance(marker, str) or not _MARKER_NAME.fullmatch(marker) or marker in (".", "..")
            or RUN_NAME.fullmatch(marker)):
        raise ValueError(f"Marker must be a plain file name: {marker!r}")
    return marker


def is_link(path: Path) -> bool:
    """True for a symbolic link, or on Windows for any reparse point (junctions included)."""
    info = os.lstat(path)
    return (stat.S_ISLNK(info.st_mode) or
            bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT))


def check_root(root: Path) -> None:
    """Refuse a relative root, or a root with a link anywhere in its existing ancestry."""
    root = Path(root)
    if not root.is_absolute():
        raise ValueError(f"Scratch root must be an absolute path: {root}")
    for directory in [*reversed(root.parents), root]:
        if os.path.lexists(directory) and is_link(directory):
            raise ValueError(f"Scratch parent is a link: {directory}")


def create(root: Path, *, marker: str = DEFAULT_MARKER) -> Path:
    """Make ``root/run-<32 hex>`` (mode 0700) holding the ownership marker, and return its path."""
    root = Path(root)
    check_marker(marker)
    check_root(root)
    root.mkdir(parents=True, exist_ok=True)
    run = root / ("run-" + uuid.uuid4().hex)
    run.mkdir(mode=0o700)
    try:
        (run / marker).write_text(run.name + "\n", encoding="ascii")
    except OSError:
        (run / marker).unlink(missing_ok=True)
        run.rmdir()
        raise
    return run


def _unescape_mount(field: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), field)


def check_mounts(run: Path) -> None:
    """Refuse a run that has a mount point at or under it."""
    # Linux bind mounts can live on the same device as their parent, so read the mount table.
    table = Path("/proc/self/mountinfo")
    if sys.platform.startswith("linux") and table.exists():
        for row in table.read_text().splitlines():
            fields = row.split(" ")
            if len(fields) < 5:
                continue
            mount = Path(_unescape_mount(fields[4]))
            if mount == run or run in mount.parents:
                raise ValueError(f"Unmount before cleanup: {mount}")
    if os.name == "posix" and not table.exists():
        def check(directory: Path) -> None:
            if directory.is_mount():
                raise ValueError(f"Unmount before cleanup: {directory}")
            for entry in directory.iterdir():
                if not entry.is_symlink() and entry.is_dir():
                    check(entry)
        check(run)


def clean(run: Path, roots: Iterable[Path], *, marker: str = DEFAULT_MARKER) -> None:
    """Remove one run made by :func:`create` under one of ``roots``.

    The path must be exactly ``<root>/run-<32 hex>``, must not be redirected, and must hold a marker
    naming itself. Links inside the run are removed without following them. A run that no longer
    exists is accepted, so a finished cleanup can be retried.
    """
    run = Path(run)
    check_marker(marker)
    roots = [Path(root) for root in roots]
    if run.parent not in roots or not RUN_NAME.fullmatch(run.name):
        raise ValueError(f"Expected the exact path returned by new: {run}")
    check_root(run.parent)
    if not os.path.lexists(run):
        return  # A caller may retry a completed cleanup.
    if is_link(run) or run.resolve(strict=True) != run:
        raise ValueError(f"Run directory was redirected: {run}")
    owner = run / marker
    if (not owner.is_file() or is_link(owner) or
            owner.read_text(encoding="ascii", errors="replace") != run.name + "\n"):
        raise ValueError(f"Run ownership marker is missing or invalid: {run}")

    check_mounts(run)
    # The marker goes last, so a run with a locked child keeps it and the cleanup can be retried.
    # On Windows shutil.rmtree (Python 3.8+) removes nested junctions without entering them.
    for entry in run.iterdir():
        if entry == owner:
            continue
        if entry.is_symlink():
            entry.unlink()
        elif is_link(entry):
            # A junction or other directory reparse point: remove the link, not its target.
            attributes = getattr(os.lstat(entry), "st_file_attributes", 0)
            if attributes & stat.FILE_ATTRIBUTE_DIRECTORY:
                entry.rmdir()
            else:
                entry.unlink()
        elif entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    owner.unlink()
    run.rmdir()
