"""Atomic file writes: a reader sees the old bytes or the new bytes, never a mix."""

from __future__ import annotations

import json
import os
import stat
import time
import uuid
from pathlib import Path
from typing import Any

_WINDOWS = os.name == "nt"
REPLACE_ATTEMPTS = 5
REPLACE_DELAY_SECONDS = 0.2


def write_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    """Replace ``path`` with ``data`` so that a crash leaves either the old or the new file.

    The bytes go to a new temporary file in the same directory, named uniquely so that
    concurrent writers never share one, then the file is flushed and fsynced, renamed over
    the target with ``os.replace``, and the directory is fsynced where the platform allows.
    Missing parent directories are created. An existing file keeps its permission bits on
    POSIX. On failure the target is left as it was and the temporary file is removed.
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        _write_synced(temporary, target, data)
        _replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    _fsync_directory(target.parent)


def write_text(path: str | os.PathLike[str], text: str) -> None:
    """Atomically write ``text`` as UTF-8. Line endings are written exactly as given."""

    write_bytes(path, text.encode("utf-8"))


def write_json(path: str | os.PathLike[str], value: Any) -> None:
    """Atomically write ``value`` as JSON: two-space indent, sorted keys, one trailing newline.

    The document is serialized before anything touches the disk, so an unserializable value
    raises without creating or changing a file. NaN and infinity are refused, because they
    are not valid JSON.
    """

    text = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    write_text(path, text)


def _write_synced(temporary: Path, target: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(temporary, flags, 0o666)
    with os.fdopen(descriptor, "wb") as handle:
        if not _WINDOWS and target.exists():
            os.fchmod(handle.fileno(), stat.S_IMODE(target.stat().st_mode))
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _replace(source: Path, target: Path) -> None:
    # Windows can refuse a replace for a moment while another process has the file open.
    attempts = REPLACE_ATTEMPTS if _WINDOWS else 1
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(REPLACE_DELAY_SECONDS)


def _fsync_directory(directory: Path) -> None:
    """Make the rename durable. Platforms and filesystems that cannot fsync a directory are skipped."""

    if _WINDOWS:
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
