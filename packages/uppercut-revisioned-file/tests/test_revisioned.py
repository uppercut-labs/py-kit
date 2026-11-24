import os
import threading
import time
from pathlib import Path

import pytest

from uppercut_revisioned_file import FileTooLarge, RevisionConflict, RevisionedFile, revision_of, write_bytes

CRLF_FILE = b"first\r\nsecond\r\nthird\r\n"


def backups_of(directory: Path, name: str) -> list[bytes]:
    return [path.read_bytes() for path in sorted(directory.iterdir()) if path.name.startswith(name + ".")]


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / "doc.txt"
    path.write_bytes(b"original\n")
    return RevisionedFile(path, backups=tmp_path / "backups", keep=3)


def test_revision_is_sixteen_hex_characters_of_the_sha256(doc):
    data, revision = doc.read()
    assert data == b"original\n"
    assert revision == revision_of(b"original\n")
    assert len(revision) == 16
    int(revision, 16)


def test_write_with_the_current_revision_replaces_the_file(doc):
    _, revision = doc.read()
    new_revision, unchanged = doc.write(b"edited\n", revision)
    assert unchanged is False
    assert new_revision == revision_of(b"edited\n")
    assert doc.path.read_bytes() == b"edited\n"


def test_stale_revision_is_refused_with_the_current_revision(doc):
    _, stale = doc.read()
    doc.write(b"someone else\n", stale)
    with pytest.raises(RevisionConflict) as caught:
        doc.write(b"my edit\n", stale)
    assert caught.value.current_revision == revision_of(b"someone else\n")
    assert doc.path.read_bytes() == b"someone else\n"


def test_identical_content_is_a_no_op_with_no_backup(doc):
    _, revision = doc.read()
    same_revision, unchanged = doc.write(b"original\n", revision)
    assert unchanged is True
    assert same_revision == revision
    assert not doc.backups.exists() or backups_of(doc.backups, doc.path.name) == []


def test_a_change_backs_up_the_previous_bytes_first(doc):
    _, revision = doc.read()
    doc.write(b"changed\n", revision)
    assert backups_of(doc.backups, doc.path.name) == [b"original\n"]


def test_backups_are_exclusive_and_keep_only_the_newest(tmp_path):
    path = tmp_path / "log.txt"
    path.write_bytes(b"v0")
    store = RevisionedFile(path, backups=tmp_path / "bk", keep=5)
    revision = revision_of(b"v0")
    for number in range(1, 26):
        content = f"v{number}".encode()
        revision, _ = store.write(content, revision)
    kept = backups_of(tmp_path / "bk", "log.txt")
    assert kept == [f"v{number}".encode() for number in range(20, 25)]


def test_same_timestamp_never_overwrites_a_backup(tmp_path, monkeypatch):
    # Two saves with an identical monotonic clock reading must still keep two backups.
    monkeypatch.setattr(time, "monotonic_ns", lambda: 0)
    path = tmp_path / "same.txt"
    path.write_bytes(b"a")
    store = RevisionedFile(path, backups=tmp_path / "bk", keep=10)
    revision = revision_of(b"a")
    revision, _ = store.write(b"b", revision)
    store.write(b"c", revision)
    assert sorted(backups_of(tmp_path / "bk", "same.txt")) == [b"a", b"b"]


def test_keep_one_retains_only_the_latest_backup(tmp_path):
    path = tmp_path / "one.txt"
    path.write_bytes(b"0")
    store = RevisionedFile(path, backups=tmp_path / "bk", keep=1)
    revision = revision_of(b"0")
    for content in (b"1", b"2", b"3"):
        revision, _ = store.write(content, revision)
    assert backups_of(tmp_path / "bk", "one.txt") == [b"2"]


def test_keep_below_one_is_refused(tmp_path):
    with pytest.raises(ValueError):
        RevisionedFile(tmp_path / "x", keep=0)


def test_default_backup_directory_sits_beside_the_file(tmp_path):
    store = RevisionedFile(tmp_path / "notes.md")
    assert store.backups == tmp_path / ".notes-backups"


def test_a_file_that_does_not_exist_can_be_created_from_the_empty_revision(tmp_path):
    store = RevisionedFile(tmp_path / "new.txt")
    revision, unchanged = store.write(b"hello\n", revision_of(b""))
    assert unchanged is False
    assert (tmp_path / "new.txt").read_bytes() == b"hello\n"
    assert not (tmp_path / ".new-backups").exists()


def test_oversized_content_is_refused_and_the_file_is_untouched(tmp_path):
    path = tmp_path / "small.txt"
    path.write_bytes(b"ok\n")
    store = RevisionedFile(path, max_bytes=8)
    _, revision = store.read()
    with pytest.raises(FileTooLarge):
        store.write(b"x" * 9, revision)
    assert path.read_bytes() == b"ok\n"


def test_a_failed_replace_leaves_the_file_and_its_backup_consistent(doc, monkeypatch):
    _, revision = doc.read()

    def refused(source, destination):
        raise OSError("disk full")

    monkeypatch.setattr("uppercut_revisioned_file._atomic.os.replace", refused)
    with pytest.raises(OSError):
        doc.write(b"never\n", revision)
    monkeypatch.undo()
    assert doc.path.read_bytes() == b"original\n"
    assert not [p for p in doc.path.parent.iterdir() if p.name.endswith(".tmp")]


def test_concurrent_writers_with_one_base_revision_only_one_wins(tmp_path):
    path = tmp_path / "race.txt"
    path.write_bytes(b"base\n")
    store = RevisionedFile(path, backups=tmp_path / "bk", keep=50)
    base = revision_of(b"base\n")
    outcomes = []
    barrier = threading.Barrier(8)

    def attempt(number):
        barrier.wait()
        try:
            store.write(f"writer {number}\n".encode(), base)
            outcomes.append("won")
        except RevisionConflict:
            outcomes.append("conflict")

    threads = [threading.Thread(target=attempt, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("won") == 1
    assert outcomes.count("conflict") == 7


# ---- line-ending repair (ENV's normalize_eol, as an option) ----

def test_line_endings_are_left_alone_by_default(tmp_path):
    path = tmp_path / "mixed.txt"
    mixed = b"a\r\nb\nc\rd\n"
    path.write_bytes(mixed)
    store = RevisionedFile(path)
    data, revision = store.read()
    assert data == mixed
    _, revision = store.read()
    store.write(b"x\ny\n", revision)
    assert path.read_bytes() == b"x\ny\n"


def test_repair_on_read_rewrites_to_the_majority_ending_after_a_backup(tmp_path):
    path = tmp_path / "mixed.txt"
    path.write_bytes(b"a\r\nb\nc\rd\r\n")
    store = RevisionedFile(path, backups=tmp_path / "bk", repair_line_endings=True)
    data, revision = store.read()
    assert data == b"a\r\nb\r\nc\r\nd\r\n"
    assert path.read_bytes() == data
    assert revision == revision_of(data)
    assert backups_of(tmp_path / "bk", "mixed.txt") == [b"a\r\nb\nc\rd\r\n"]


def test_a_repaired_file_accepts_a_save_made_from_the_returned_revision(tmp_path):
    path = tmp_path / "mixed.txt"
    path.write_bytes(b"one\ntwo\r\nthree\n")
    store = RevisionedFile(path, backups=tmp_path / "bk", repair_line_endings=True)
    _, revision = store.read()
    new_revision, unchanged = store.write(b"one\ntwo\nthree\nfour\n", revision)
    assert unchanged is False
    assert path.read_bytes() == b"one\ntwo\nthree\nfour\n"
    assert new_revision == revision_of(path.read_bytes())


def test_repair_still_serves_the_repaired_bytes_when_the_rewrite_fails(tmp_path, monkeypatch):
    path = tmp_path / "stuck.txt"
    mixed = b"a\r\nb\n"
    path.write_bytes(mixed)
    store = RevisionedFile(path, backups=tmp_path / "bk", repair_line_endings=True)

    def read_only(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr("uppercut_revisioned_file._revisioned.write_bytes", read_only)
    data, revision = store.read()
    monkeypatch.undo()
    assert data == b"a\r\nb\r\n"
    assert revision == revision_of(mixed)
    assert path.read_bytes() == mixed
    # The next save is based on the on-disk revision and writes the repaired content.
    store.write(data, revision)
    assert path.read_bytes() == b"a\r\nb\r\n"


def test_save_converts_incoming_line_endings_to_the_file_ending(tmp_path):
    path = tmp_path / "crlf.txt"
    path.write_bytes(b"alpha\r\nbeta\r\n")
    store = RevisionedFile(path, backups=tmp_path / "bk", repair_line_endings=True)
    _, revision = store.read()
    store.write(b"gamma\ndelta\repsilon\n", revision)
    assert path.read_bytes() == b"gamma\r\ndelta\r\nepsilon\r\n"


def test_save_into_a_file_with_no_line_breaks_keeps_the_content_ending(tmp_path):
    path = tmp_path / "single.txt"
    path.write_bytes(b"no break")
    store = RevisionedFile(path, backups=tmp_path / "bk", repair_line_endings=True)
    _, revision = store.read()
    store.write(b"one\ntwo\n", revision)
    assert path.read_bytes() == b"one\ntwo\n"


def test_non_utf8_content_is_never_rewritten(tmp_path):
    path = tmp_path / "blob.bin"
    binary = b"\xff\xfe\r\n\x00\n\r"
    path.write_bytes(binary)
    store = RevisionedFile(path, backups=tmp_path / "bk", repair_line_endings=True)
    data, revision = store.read()
    assert data == binary
    store.write(b"\xff\n", revision)
    assert path.read_bytes() == b"\xff\n"


def test_the_repaired_content_counts_against_max_bytes(tmp_path):
    path = tmp_path / "limit.txt"
    path.write_bytes(b"ab\r\n")
    store = RevisionedFile(path, max_bytes=5, repair_line_endings=True)
    _, revision = store.read()
    # Five bytes of LF content pass the first check, then become six once the file's CRLF is applied.
    with pytest.raises(FileTooLarge):
        store.write(b"ab\ncd", revision)
    assert path.read_bytes() == b"ab\r\n"
