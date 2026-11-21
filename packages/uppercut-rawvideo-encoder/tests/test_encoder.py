import json
import shutil
import subprocess
import sys
from fractions import Fraction

import pytest
from PIL import Image

from uppercut_rawvideo_encoder import EncodeError, RawVideoEncoder, ffmpeg_command, font, format_fps, nearest

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")

SIZE = (64, 48)


def solid(value, size=SIZE):
    return Image.new("RGB", size, (value, 40, 200 - value))


def fake_ffmpeg(tmp_path, body):
    """A stand-in for ffmpeg: a Python script that records its arguments, then runs `body`."""
    script = tmp_path / "fake_ffmpeg.py"
    script.write_text(
        "import json, os, sys\n"
        f"open({str(tmp_path / 'argv.json')!r}, 'w').write(json.dumps(sys.argv[1:]))\n" + body
    )
    return [sys.executable, str(script)]


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,pix_fmt,width,height,r_frame_rate,nb_read_frames",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)["streams"][0]


def test_command_always_has_nostdin_and_the_given_quality(tmp_path):
    cmd = ffmpeg_command(tmp_path / "o.mp4", (3840, 2160), 60, crf=16, preset="medium")
    assert cmd[:4] == ["ffmpeg", "-nostdin", "-y", "-loglevel"]
    assert cmd[cmd.index("-s") + 1] == "3840x2160"
    assert cmd[cmd.index("-r") + 1] == "60"
    assert cmd[cmd.index("-crf") + 1] == "16"
    assert cmd[cmd.index("-preset") + 1] == "medium"
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-pix_fmt", cmd.index("-c:v")) + 1] == "yuv420p"
    assert cmd[-1] == str(tmp_path / "o.mp4")


def test_fps_crf_and_preset_have_no_defaults(tmp_path):
    with pytest.raises(TypeError):
        RawVideoEncoder(tmp_path / "o.mp4", SIZE, crf=16, preset="medium")  # no fps
    with pytest.raises(TypeError):
        RawVideoEncoder(tmp_path / "o.mp4", SIZE, 60, preset="medium")  # no crf
    with pytest.raises(TypeError):
        RawVideoEncoder(tmp_path / "o.mp4", SIZE, 60, crf=16)  # no preset


def test_format_fps():
    assert format_fps(60) == "60"
    assert format_fps(24.0) == "24"
    assert format_fps(59.94) == "59.94"
    assert format_fps(Fraction(60000, 1001)) == "60000/1001"
    assert format_fps("24000/1001") == "24000/1001"
    for bad in (0, -1, "0/1", "sixty", float("nan")):
        with pytest.raises(ValueError):
            format_fps(bad)
    with pytest.raises(TypeError):
        format_fps(None)  # type: ignore[arg-type]


def test_extra_inputs_and_output_args_land_between_the_frame_input_and_the_file(tmp_path):
    cmd = ffmpeg_command(
        tmp_path / "o.mp4", SIZE, 24, crf=14, preset="slow",
        extra_inputs=["-ss", "1.5", "-i", "song.wav"],
        output_args=["-map", "0:v", "-map", "1:a", "-c:a", "aac"],
        ffmpeg=["python", "-m", "fakeff"],
    )
    assert cmd[:3] == ["python", "-m", "fakeff"]
    first_input = cmd.index("-i")
    assert cmd[first_input + 1] == "-"
    assert cmd[first_input + 2:first_input + 6] == ["-ss", "1.5", "-i", "song.wav"]
    assert cmd.index("-crf") > first_input + 5
    assert cmd[-7:-1] == ["-map", "0:v", "-map", "1:a", "-c:a", "aac"]


def test_failed_ffmpeg_raises_with_exit_code_and_stderr_tail(tmp_path):
    ff = fake_ffmpeg(tmp_path, "sys.stdin.buffer.read()\nsys.stderr.write('x264 [error]: boom\\n')\nsys.exit(7)\n")
    out = tmp_path / "o.mp4"
    enc = RawVideoEncoder(out, SIZE, 60, crf=16, preset="medium", ffmpeg=ff)
    enc.write(solid(10))
    with pytest.raises(EncodeError) as info:
        enc.close()
    err = info.value
    assert err.returncode == 7
    assert "boom" in err.stderr_tail
    assert "boom" in str(err) and "code 7" in str(err)
    assert err.frames == 1
    assert not out.exists()


def test_ffmpeg_dying_mid_render_raises_encode_error_not_a_broken_pipe(tmp_path):
    ff = fake_ffmpeg(tmp_path, "sys.stderr.write('Invalid argument\\n')\nsys.exit(3)\n")
    out = tmp_path / "o.mp4"
    out.write_bytes(b"partial")
    with pytest.raises(EncodeError) as info:
        with RawVideoEncoder(out, SIZE, 60, crf=16, preset="medium", ffmpeg=ff) as enc:
            for _ in range(500):  # more than the pipe buffer holds
                enc.write(solid(1))
    assert info.value.returncode == 3
    assert "Invalid argument" in info.value.stderr_tail
    assert not out.exists()


def test_missing_ffmpeg_executable_raises_encode_error(tmp_path):
    with pytest.raises(EncodeError, match="could not start"):
        RawVideoEncoder(tmp_path / "o.mp4", SIZE, 60, crf=16, preset="medium", ffmpeg=str(tmp_path / "no-such-ffmpeg"))


def test_stderr_tail_is_bounded(tmp_path):
    ff = fake_ffmpeg(tmp_path, "sys.stdin.buffer.read()\nsys.stderr.write('a' * 100000 + 'THE END')\nsys.exit(1)\n")
    enc = RawVideoEncoder(tmp_path / "o.mp4", SIZE, 60, crf=16, preset="medium", ffmpeg=ff, tail_bytes=100)
    enc.write(solid(1))
    with pytest.raises(EncodeError) as info:
        enc.close()
    assert len(info.value.stderr_tail) <= 100
    assert info.value.stderr_tail.endswith("THE END")


def test_no_frames_is_an_error_even_when_ffmpeg_exits_zero(tmp_path):
    ff = fake_ffmpeg(tmp_path, "sys.stdin.buffer.read()\n")
    with pytest.raises(EncodeError, match="no frames"):
        with RawVideoEncoder(tmp_path / "o.mp4", SIZE, 60, crf=16, preset="medium", ffmpeg=ff):
            pass


def test_exception_in_the_body_aborts_and_propagates(tmp_path):
    ff = fake_ffmpeg(tmp_path, "sys.stdin.buffer.read()\nopen(sys.argv[-1], 'wb').write(b'partial')\n")
    out = tmp_path / "o.mp4"
    with pytest.raises(KeyError):
        with RawVideoEncoder(out, SIZE, 60, crf=16, preset="medium", ffmpeg=ff) as enc:
            enc.write(solid(1))
            raise KeyError("render bug")
    assert not out.exists()


def test_ffmpeg_stdin_is_not_the_terminal(tmp_path):
    """-nostdin means ffmpeg ignores keystrokes; the frames come over the pipe and arrive intact."""
    ff = fake_ffmpeg(tmp_path, "data = sys.stdin.buffer.read()\nopen(sys.argv[-1], 'wb').write(data)\n")
    out = tmp_path / "raw.bin"
    with RawVideoEncoder(out, SIZE, 60, crf=16, preset="medium", ffmpeg=ff) as enc:
        enc.write(solid(10))
        enc.write(bytes(range(256)) * (SIZE[0] * SIZE[1] * 3 // 256))
    argv = json.loads((tmp_path / "argv.json").read_text())
    assert "-nostdin" in argv
    data = out.read_bytes()
    assert data[: SIZE[0] * SIZE[1] * 3] == solid(10).tobytes()
    assert len(data) == 2 * SIZE[0] * SIZE[1] * 3


def test_wrong_frame_size_is_rejected(tmp_path):
    ff = fake_ffmpeg(tmp_path, "sys.stdin.buffer.read()\n")
    enc = RawVideoEncoder(tmp_path / "o.mp4", SIZE, 60, crf=16, preset="medium", ffmpeg=ff)
    with pytest.raises(ValueError, match="64x48"):
        enc.write(solid(1, (65, 48)))
    with pytest.raises(ValueError, match="bytes"):
        enc.write(b"\x00" * 10)
    enc.abort()
    with pytest.raises(ValueError, match="closed"):
        enc.write(solid(1))


def test_non_rgb_images_are_converted(tmp_path):
    ff = fake_ffmpeg(tmp_path, "data = sys.stdin.buffer.read()\nopen(sys.argv[-1], 'wb').write(data)\n")
    out = tmp_path / "raw.bin"
    with RawVideoEncoder(out, SIZE, 60, crf=16, preset="medium", ffmpeg=ff) as enc:
        enc.write(Image.new("RGBA", SIZE, (1, 2, 3, 255)))
    assert out.read_bytes() == bytes([1, 2, 3]) * (SIZE[0] * SIZE[1])


def test_objects_with_tobytes_are_accepted(tmp_path):
    ff = fake_ffmpeg(tmp_path, "data = sys.stdin.buffer.read()\nopen(sys.argv[-1], 'wb').write(data)\n")

    class Pixels:
        def tobytes(self):
            return bytes([9, 8, 7]) * (SIZE[0] * SIZE[1])

    out = tmp_path / "raw.bin"
    with RawVideoEncoder(out, SIZE, 60, crf=16, preset="medium", ffmpeg=ff) as enc:
        enc.write(Pixels())
    assert out.read_bytes() == bytes([9, 8, 7]) * (SIZE[0] * SIZE[1])


@needs_ffmpeg
def test_encodes_a_playable_h264_yuv420p_file(tmp_path):
    out = tmp_path / "clip.mp4"
    with RawVideoEncoder(out, (64, 48), 24, crf=18, preset="ultrafast") as enc:
        for i in range(12):
            enc.write(solid(i * 10))
    assert enc.frames == 12
    info = probe(out)
    assert (info["codec_name"], info["pix_fmt"]) == ("h264", "yuv420p")
    assert (info["width"], info["height"], info["r_frame_rate"]) == (64, 48, "24/1")
    assert info["nb_read_frames"] == "12"


@needs_ffmpeg
def test_real_ffmpeg_failure_is_reported_with_its_message(tmp_path):
    out = tmp_path / "missing-folder" / "clip.mp4"
    with pytest.raises(EncodeError) as info:
        with RawVideoEncoder(out, (64, 48), 60, crf=16, preset="ultrafast") as enc:
            enc.write(solid(1))
    assert info.value.returncode != 0
    assert info.value.stderr_tail  # ffmpeg said why


@needs_ffmpeg
def test_soundtrack_through_extra_inputs(tmp_path):
    wav = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(wav)],
        check=True,
    )
    out = tmp_path / "clip.mp4"
    with RawVideoEncoder(
        out, (64, 48), 24, crf=18, preset="ultrafast",
        extra_inputs=["-i", str(wav)],
        output_args=["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-shortest"],
    ) as enc:
        for i in range(24):
            enc.write(solid(i))
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert sorted(streams) == ["audio", "video"]


def test_nearest_scales_by_whole_factors_without_smoothing():
    img = Image.new("RGB", (2, 1))
    img.putpixel((0, 0), (255, 0, 0))
    img.putpixel((1, 0), (0, 0, 255))
    big = nearest(img, 3)
    assert big.size == (6, 3)
    assert {big.getpixel((x, y)) for x in range(3) for y in range(3)} == {(255, 0, 0)}
    assert {big.getpixel((x, y)) for x in range(3, 6) for y in range(3)} == {(0, 0, 255)}
    for bad in (0, -2, 1.5, True):
        with pytest.raises(ValueError):
            nearest(img, bad)  # type: ignore[arg-type]


def test_font_is_loaded_once_per_file_size_and_index(monkeypatch):
    from uppercut_rawvideo_encoder import _pixels

    calls = []

    def fake_truetype(path, size, index=0):
        calls.append((path, size, index))
        return object()

    monkeypatch.setattr(_pixels.ImageFont, "truetype", fake_truetype)
    font.cache_clear()
    a = font("a.ttf", 30)
    assert font("a.ttf", 30) is a
    assert font("a.ttf", 31) is not a
    assert font("a.ttf", 30, index=1) is not a
    assert calls == [("a.ttf", 30, 0), ("a.ttf", 31, 0), ("a.ttf", 30, 1)]
    font.cache_clear()
