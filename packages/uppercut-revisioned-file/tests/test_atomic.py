import errno
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from uppercut_revisioned_file import write_bytes, write_json, write_text
from uppercut_revisioned_file import _atomic

POSIX = os.name != "nt"
posix_only = pytest.mark.skipif(not POSIX, reason="POSIX permission bits and directory fsync")


def leftovers(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir() if entry.name.endswith(".tmp"))


@pytest.fixture
def replaces(monkeypatch):
    """Record every os.replace call and make the real one run."""

    calls = []
    real_replace = os.replace

    def recording(source, target):
        calls.append((os.fspath(source), os.fspath(target)))
        return real_replace(source, target)

    monkeypatch.setattr(_atomic.os, "replace", recording)
    return calls


def test_write_bytes_creates_missing_parents_and_writes_exact_bytes(tmp_path):
    target = tmp_path / "a" / "b" / "data.bin"
    payload = bytes(range(256)) + b"\r\n\x00"
    write_bytes(target, payload)
    assert target.read_bytes() == payload
    assert leftovers(target.parent) == []


def test_write_text_is_utf8_and_keeps_line_endings_as_given(tmp_path):
    target = tmp_path / "notes.txt"
    write_text(target, "café\r\nline two\nlone\rcr")
    assert target.read_bytes() == "café\r\nline two\nlone\rcr".encode("utf-8")


def test_write_json_matches_the_job_tracking_format(tmp_path):
    # The JSON layout that earlier local copies used: indent 2, sorted keys, one trailing newline.
    target = tmp_path / "manifest.json"
    write_json(target, {"b": [1, {"y": None, "x": True}], "a": "café"})
    expected = json.dumps({"b": [1, {"y": None, "x": True}], "a": "café"}, indent=2, sort_keys=True) + "\n"
    assert target.read_bytes() == expected.encode("utf-8")
    assert json.loads(target.read_text(encoding="utf-8")) == {"b": [1, {"y": None, "x": True}], "a": "café"}


def test_write_json_refuses_nan_before_touching_the_disk(tmp_path):
    target = tmp_path / "state.json"
    target.write_text('{"kept": true}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        write_json(target, {"value": float("nan")})
    assert target.read_text(encoding="utf-8") == '{"kept": true}\n'
    assert leftovers(tmp_path) == []


def test_write_json_leaves_no_file_when_serialization_fails(tmp_path):
    target = tmp_path / "never.json"
    with pytest.raises(TypeError):
        write_json(target, {"when": object()})
    assert not target.exists()
    assert leftovers(tmp_path) == []


def test_overwrite_replaces_the_whole_file(tmp_path):
    target = tmp_path / "f.txt"
    write_text(target, "a much longer first version of the file\n")
    write_text(target, "short\n")
    assert target.read_text(encoding="utf-8") == "short\n"


def test_temporary_name_is_unique_per_write_and_beside_the_target(tmp_path, replaces):
    target = tmp_path / "state.json"
    write_json(target, {"n": 1})
    write_json(target, {"n": 2})
    sources = [Path(source) for source, destination in replaces if destination == str(target)]
    assert len(sources) == 2
    assert sources[0] != sources[1]
    for source in sources:
        assert source.parent == tmp_path
        assert source.name.startswith(".state.json.")
        assert source.name.endswith(".tmp")


def test_fixed_tmp_name_is_never_clobbered(tmp_path):
    # An earlier copy wrote to "<name>.tmp" and replaced it. A file that already has that name
    # (another writer's temporary file, or a user's) must survive a write untouched.
    target = tmp_path / "workspace.json"
    other_writers_file = tmp_path / "workspace.json.tmp"
    other_writers_file.write_bytes(b"another writer's partial bytes")
    write_bytes(target, b"{}\n")
    assert target.read_bytes() == b"{}\n"
    assert other_writers_file.read_bytes() == b"another writer's partial bytes"


def test_fsync_happens_before_the_replace(tmp_path, monkeypatch):
    # An earlier copy skipped fsync. The file must be flushed and synced before it is renamed.
    events = []
    real_fsync = os.fsync
    real_replace = os.replace

    def fsync(fd):
        events.append("fsync-dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "fsync-file")
        return real_fsync(fd)

    def replace(source, target):
        events.append("replace")
        return real_replace(source, target)

    monkeypatch.setattr(_atomic.os, "fsync", fsync)
    monkeypatch.setattr(_atomic.os, "replace", replace)
    write_bytes(tmp_path / "x.bin", b"payload")
    assert events.index("fsync-file") < events.index("replace")
    if POSIX:
        assert events.index("replace") < events.index("fsync-dir")


def test_failed_fsync_keeps_the_old_file_and_removes_the_temporary(tmp_path, monkeypatch):
    target = tmp_path / "keep.json"
    write_json(target, {"version": 1})

    def broken_fsync(fd):
        raise OSError(errno.EIO, "disk went away")

    monkeypatch.setattr(_atomic.os, "fsync", broken_fsync)
    with pytest.raises(OSError):
        write_json(target, {"version": 2})
    monkeypatch.undo()
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 1}
    assert leftovers(tmp_path) == []


def test_failed_replace_keeps_the_old_file(tmp_path, monkeypatch):
    target = tmp_path / "keep.txt"
    write_text(target, "old\n")

    def refused(source, destination):
        raise OSError(errno.EXDEV, "cross-device")

    monkeypatch.setattr(_atomic.os, "replace", refused)
    with pytest.raises(OSError):
        write_text(target, "new\n")
    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "old\n"
    assert leftovers(tmp_path) == []


def test_windows_replace_is_retried_while_the_file_is_locked(tmp_path, monkeypatch):
    monkeypatch.setattr(_atomic, "_WINDOWS", True)
    monkeypatch.setattr(_atomic.time, "sleep", lambda seconds: None)
    real_replace = os.replace
    attempts = []

    def locked_twice(source, target):
        attempts.append(1)
        if len(attempts) <= 2:
            raise PermissionError(errno.EACCES, "file is open elsewhere")
        return real_replace(source, target)

    monkeypatch.setattr(_atomic.os, "replace", locked_twice)
    target = tmp_path / "locked.txt"
    write_text(target, "saved after retries\n")
    assert len(attempts) == 3
    assert target.read_text(encoding="utf-8") == "saved after retries\n"


def test_windows_replace_gives_up_after_five_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(_atomic, "_WINDOWS", True)
    monkeypatch.setattr(_atomic.time, "sleep", lambda seconds: None)
    attempts = []

    def always_locked(source, target):
        attempts.append(1)
        raise PermissionError(errno.EACCES, "still open")

    monkeypatch.setattr(_atomic.os, "replace", always_locked)
    with pytest.raises(PermissionError):
        write_text(tmp_path / "busy.txt", "x")
    assert len(attempts) == _atomic.REPLACE_ATTEMPTS
    assert leftovers(tmp_path) == []


def test_posix_replace_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(_atomic, "_WINDOWS", False)
    attempts = []

    def denied(source, target):
        attempts.append(1)
        raise PermissionError(errno.EACCES, "read-only directory")

    monkeypatch.setattr(_atomic.os, "replace", denied)
    with pytest.raises(PermissionError):
        write_text(tmp_path / "ro.txt", "x")
    assert len(attempts) == 1


def test_directory_fsync_failure_does_not_fail_the_write(tmp_path, monkeypatch):
    real_fsync = os.fsync

    def refuse_directories(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "directory fsync not supported here")
        return real_fsync(fd)

    monkeypatch.setattr(_atomic.os, "fsync", refuse_directories)
    target = tmp_path / "ok.txt"
    write_text(target, "written anyway\n")
    assert target.read_text(encoding="utf-8") == "written anyway\n"


@posix_only
def test_existing_file_keeps_its_permission_bits(tmp_path):
    target = tmp_path / "shared.txt"
    target.write_text("before\n", encoding="utf-8")
    os.chmod(target, 0o640)
    write_text(target, "after\n")
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


@posix_only
def test_new_file_gets_the_umask_default_not_a_private_mode(tmp_path):
    target = tmp_path / "fresh.txt"
    write_text(target, "fresh\n")
    umask = os.umask(0)
    os.umask(umask)
    assert stat.S_IMODE(target.stat().st_mode) == 0o666 & ~umask
