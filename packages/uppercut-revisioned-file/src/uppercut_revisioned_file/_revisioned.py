"""One file edited whole by many writers, guarded by an opaque revision."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from ._atomic import _fsync_directory, write_bytes

DEFAULT_KEEP = 20
_NEWLINE = re.compile(r"\r\n|\r|\n")
DEFAULT_MAX_BYTES = 1024 * 1024


class RevisionConflict(Exception):
    """The file changed after the caller loaded it. ``current_revision`` is the revision now on disk."""

    def __init__(self, current_revision: str) -> None:
        super().__init__("file changed since it was loaded")
        self.current_revision = current_revision


class FileTooLarge(ValueError):
    """The content, after any line-ending repair, is larger than ``max_bytes``."""


def revision_of(data: bytes) -> str:
    """The opaque revision of ``data``: 16 hex characters of its SHA-256. Clients should not parse it."""

    return hashlib.sha256(data).hexdigest()[:16]


class RevisionedFile:
    """Read and replace one file whole, with optimistic concurrency.

    ``read()`` returns ``(bytes, revision)``. ``write(data, base_revision)`` succeeds only when
    the file still has ``base_revision``; otherwise it raises ``RevisionConflict`` carrying the
    current revision and leaves the file alone. A changed file is first copied into the
    backups directory (exclusive, fsynced, the newest ``keep`` kept), then replaced atomically.
    Writing bytes equal to the file is a no-op that returns ``(revision, True)``.

    With ``repair_line_endings=True``, lines that mix CRLF, LF and lone CR are rewritten to the
    file's majority ending. ``read()`` repairs the file on disk (after a backup) and returns the
    repaired bytes with the new revision. ``write()`` converts incoming line endings to the
    file's ending, so a save never introduces a mix. Content that is not UTF-8 is left alone.

    Calls on one instance are serialized by a lock. Another program that edits the file between
    the revision check and the replace can still race it.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        backups: str | os.PathLike[str] | None = None,
        keep: int = DEFAULT_KEEP,
        max_bytes: int = DEFAULT_MAX_BYTES,
        repair_line_endings: bool = False,
    ) -> None:
        if keep < 1:
            raise ValueError("keep must be at least 1")
        self.path = Path(path)
        self.backups = Path(backups) if backups is not None else self.path.parent / f".{self.path.stem}-backups"
        self.keep = keep
        self.max_bytes = max_bytes
        self.repair_line_endings = repair_line_endings
        self._lock = threading.Lock()

    def read(self) -> tuple[bytes, str]:
        with self._lock:
            data = self.path.read_bytes()
            revision = revision_of(data)
            if self.repair_line_endings:
                healed = _with_one_line_ending(data)
                if healed is not None and healed != data:
                    try:
                        self._backup(data)
                        write_bytes(self.path, healed)
                    except OSError:
                        # Serve the repaired bytes under the on-disk revision, so the next
                        # write, based on that revision, replaces the mixed endings.
                        return healed, revision
                    data, revision = healed, revision_of(healed)
            return data, revision

    def write(self, data: bytes, base_revision: str) -> tuple[str, bool]:
        """Replace the file with ``data`` if it still has ``base_revision``. Returns ``(revision, unchanged)``."""

        if len(data) > self.max_bytes:
            raise FileTooLarge(f"content is larger than {self.max_bytes} bytes")
        with self._lock:
            current = self.path.read_bytes() if self.path.exists() else b""
            current_revision = revision_of(current)
            if current_revision != base_revision:
                raise RevisionConflict(current_revision)
            if self.repair_line_endings:
                data = _with_file_line_ending(current, data)
                if len(data) > self.max_bytes:
                    raise FileTooLarge(f"content is larger than {self.max_bytes} bytes")
            if data == current:
                return current_revision, True
            if self.path.exists():
                self._backup(current)
            write_bytes(self.path, data)
            return revision_of(data), False

    def _backup(self, data: bytes) -> None:
        self.backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        # mkstemp creates the file exclusively, so two saves can never share a backup name,
        # and the file is private on POSIX, which matters when it holds secrets.
        descriptor, name = tempfile.mkstemp(
            prefix=f"{self.path.name}.{stamp}.{time.monotonic_ns():020d}.",
            suffix=".bak",
            dir=self.backups,
        )
        written = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            written.unlink(missing_ok=True)
            raise
        _fsync_directory(self.backups)
        older = sorted(
            candidate
            for candidate in self.backups.iterdir()
            if candidate != written
            and candidate.name.startswith(self.path.name + ".")
            and candidate.name.endswith(".bak")
            and candidate.is_file()
        )
        # The backup just written always counts as one of the kept ones.
        obsolete = older if self.keep == 1 else older[: -(self.keep - 1)]
        for candidate in obsolete:
            candidate.unlink(missing_ok=True)


def _majority_ending(text: str) -> str:
    """The ending a file mostly uses. Ties go to CRLF. No line break at all uses the OS default."""

    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    if lf > crlf:
        return "\n"
    if crlf:
        return "\r\n"
    return "\r\n" if os.name == "nt" else "\n"


def _normalized(text: str, ending: str) -> str:
    return _NEWLINE.sub(ending, text)


def _with_one_line_ending(data: bytes) -> bytes | None:
    """``data`` with every line break set to the file's majority ending, or None if it is not UTF-8."""

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return _normalized(text, _majority_ending(text)).encode("utf-8")


def _with_file_line_ending(current: bytes, data: bytes) -> bytes:
    """``data`` with its line breaks set to the ending already used by ``current``."""

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    existing = current.decode("utf-8", "replace")
    ending = _majority_ending(existing) if ("\r" in existing or "\n" in existing) else _majority_ending(text)
    return _normalized(text, ending).encode("utf-8")

