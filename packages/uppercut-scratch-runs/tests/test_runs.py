"""Cleanup against disposable fixtures on the current host, ported from the console lab's tests."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from uppercut_scratch_runs import DEFAULT_MARKER, check_root, clean, create

MARKER = ".example-run"
WINDOWS = os.name == "nt"


@pytest.fixture
def root(tmp_path):
    return tmp_path / "managed"


@pytest.fixture
def run(root):
    return create(root, marker=MARKER)


def link_directory(link, target):
    if WINDOWS:
        # Windows junctions work for a non-elevated user.
        subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, stdout=subprocess.DEVNULL)
    else:
        link.symlink_to(target, target_is_directory=True)


def test_create_writes_a_marker_naming_the_run(root, run):
    assert run.parent == root
    assert len(run.name) == 36 and run.name.startswith("run-")
    assert (run / MARKER).read_text(encoding="ascii") == run.name + "\n"
    if not WINDOWS:
        assert run.stat().st_mode & 0o777 == 0o700


def test_default_marker(root):
    run = create(root)
    assert (run / DEFAULT_MARKER).is_file()
    clean(run, [root])
    assert not run.exists()


def test_nested_cleanup_preserves_sibling_and_can_be_retried(root, run):
    deep = run / "output" / "objects"
    deep.mkdir(parents=True)
    (deep / "file").write_bytes(b"work")
    (run / "top.txt").write_text("top")
    sibling = create(root, marker=MARKER)
    clean(run, [root], marker=MARKER)
    clean(run, [root], marker=MARKER)
    assert not run.exists()
    assert (sibling / MARKER).is_file()


def test_refuses_unowned_locations(root, run):
    sibling = root / ("run-" + "1" * 32)
    sibling.mkdir()
    for target in (root, run.parent.parent, sibling, root / "run-short"):
        with pytest.raises(ValueError):
            clean(target, [root], marker=MARKER)
    with pytest.raises(ValueError):
        clean(run, [root])  # The default marker is not this run's marker.
    (run / MARKER).write_text("wrong run")
    with pytest.raises(ValueError):
        clean(run, [root], marker=MARKER)
    assert run.exists()
    assert sibling.exists()


def test_refuses_a_run_outside_the_given_roots(tmp_path, run):
    with pytest.raises(ValueError):
        clean(run, [tmp_path / "elsewhere"], marker=MARKER)
    assert run.exists()


def test_links_never_delete_their_destination(root, run):
    outside = root.parent / "retained"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep")
    nested = run / "nested"
    nested.mkdir()
    link_directory(nested / "link", outside)
    link_directory(run / "top-link", outside)
    redirected = root / ("run-" + "2" * 32)
    link_directory(redirected, run)
    with pytest.raises(ValueError):
        clean(redirected, [root], marker=MARKER)
    clean(run, [root], marker=MARKER)
    assert sentinel.read_text() == "keep"
    assert not run.exists()


def test_file_links_are_removed_not_followed(root, run):
    outside = root.parent / "keep.txt"
    outside.write_text("keep")
    try:
        (run / "file-link").symlink_to(outside)
    except OSError:
        pytest.skip("this account cannot create file symlinks")
    clean(run, [root], marker=MARKER)
    assert outside.read_text() == "keep"


def test_refuses_a_root_below_a_link(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    link_directory(linked, real)
    with pytest.raises(ValueError):
        create(linked / "managed", marker=MARKER)
    with pytest.raises(ValueError):
        check_root(linked)
    assert not (real / "managed").exists()


def test_refuses_a_relative_root():
    with pytest.raises(ValueError):
        create(Path("relative"), marker=MARKER)


@pytest.mark.parametrize("marker", ["", ".", "..", "a/b", "a\\b", "run-" + "0" * 32])
def test_refuses_markers_that_are_not_plain_names(root, marker):
    with pytest.raises(ValueError):
        create(root, marker=marker)


@pytest.mark.skipif(not WINDOWS, reason="Windows keeps open files from being deleted")
def test_locked_file_keeps_the_marker_so_cleanup_can_be_retried(root, run):
    nested = run / "nested"
    nested.mkdir()
    held = (nested / "held.bin").open("wb")
    try:
        with pytest.raises(OSError):
            clean(run, [root], marker=MARKER)
        assert (run / MARKER).is_file()
    finally:
        held.close()
    clean(run, [root], marker=MARKER)
    assert not run.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="reads the Linux mount table")
def test_mount_table_parse_does_not_refuse_an_ordinary_run(root, run):
    clean(run, [root], marker=MARKER)
    assert not run.exists()
