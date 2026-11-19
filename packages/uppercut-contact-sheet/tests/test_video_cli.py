import shutil
import subprocess

import pytest
from PIL import Image

from uppercut_contact_sheet import (
    ffmpeg_available,
    format_time,
    frame_times,
    grab_frame,
    parse_time,
    probe_duration,
    video_tiles,
)
from uppercut_contact_sheet.cli import main

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg/ffprobe not installed")


def test_parse_and_format_time():
    assert parse_time("12.5") == 12.5
    assert parse_time("1:05") == 65
    assert parse_time("1:02:03") == 3723
    assert format_time(65) == "1:05"
    assert format_time(5.5) == "0:05.50"


def test_frame_times_every_and_count():
    assert frame_times(10, every=5) == [0, 5, 10]
    assert frame_times(10, every=4, start=1, end=9) == [1, 5, 9]
    assert frame_times(10, count=4) == [1.25, 3.75, 6.25, 8.75]
    assert frame_times(10, count=2, end=100) == [2.5, 7.5]  # end clamps to duration


def test_frame_times_errors():
    with pytest.raises(ValueError):
        frame_times(10)
    with pytest.raises(ValueError):
        frame_times(10, every=1, count=2)
    with pytest.raises(ValueError):
        frame_times(10, every=0)
    with pytest.raises(ValueError):
        frame_times(10, count=0)
    with pytest.raises(ValueError):
        frame_times(10, every=1, start=10)


def test_cli_images(tmp_path, capsys):
    d = tmp_path / "imgs"
    d.mkdir()
    for i in range(3):
        Image.new("RGB", (30, 20), (i * 80, 0, 0)).save(d / f"{i}.png")
    out = tmp_path / "sheet.png"
    assert main([str(d), "-o", str(out), "--cols", "2", "--cell", "40x30", "--title", "T"]) == 0
    assert out.exists()
    assert str(out) in capsys.readouterr().out


def test_cli_paged(tmp_path):
    files = []
    for i in range(3):
        p = tmp_path / f"{i}.png"
        Image.new("RGB", (10, 10)).save(p)
        files.append(str(p))
    assert main([*files, "-o", str(tmp_path / "out" / "s.jpg"), "--per-sheet", "2", "--cell", "20x20"]) == 0
    assert (tmp_path / "out" / "s_01.jpg").exists() and (tmp_path / "out" / "s_02.jpg").exists()


def test_cli_errors(tmp_path, capsys):
    assert main(["-o", str(tmp_path / "x.png")]) == 1
    assert main([str(tmp_path), "-o", str(tmp_path / "x.png")]) == 1  # empty folder
    assert "no images" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["a.png", "-o", "x.png", "--cell", "nope"])


@needs_ffmpeg
def test_video_frames_and_cli(tmp_path):
    video = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=2",
         "-pix_fmt", "yuv420p", str(video)],
        check=True,
    )
    assert 1.5 < probe_duration(video) < 2.5
    assert grab_frame(video, 1.0, 80).width == 80
    assert grab_frame(video, probe_duration(video)).size == (160, 90)  # seek at the very end steps back
    tiles = video_tiles(video, [0, 1], width=64)
    assert [t.label for t in tiles] == ["0:00", "0:01"]
    out = tmp_path / "v.jpg"
    assert main(["--video", str(video), "--count", "4", "-o", str(out), "--cell", "64x40"]) == 0
    assert out.exists()
    assert main(["--video", str(video), "-o", str(out)]) == 1  # no sampling mode


def test_missing_binary_message(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert not ffmpeg_available()
    with pytest.raises(RuntimeError, match="ffmpeg"):
        grab_frame(tmp_path / "x.mp4", 0)
    with pytest.raises(RuntimeError, match="ffprobe"):
        probe_duration(tmp_path / "x.mp4")
