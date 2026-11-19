import json
import math
import shutil
import subprocess

import pytest

from uppercut_ffmpeg_loudness import (
    LoudnessError,
    Measure,
    apply_gain,
    common_target,
    limiter_filter,
    loudnorm_filter,
    measure,
    measure_loudnorm,
    normalize,
    parse_ebur128_summary,
    parse_loudnorm_json,
    plan_gain,
    volume_filter,
)
from uppercut_ffmpeg_loudness.cli import main as cli_main

SUMMARY = """\
[Parsed_ebur128_0 @ 0x1] t: 1.9  TARGET:-23 LUFS    M: -21.8 S:-120.7     I: -50.0 LUFS       LRA:   9.0 LU
[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -21.8 LUFS
    Threshold: -31.8 LUFS

  Loudness range:
    LRA:         3.5 LU
    Threshold:   0.0 LUFS
    LRA low:     0.0 LUFS
    LRA high:    0.0 LUFS

  True peak:
    Peak:      -18.1 dBFS
[out#0/null @ 0x2] video:0KiB audio:172KiB
"""

LOUDNORM = """\
[Parsed_loudnorm_0 @ 0x77]
{
\t"input_i" : "-21.75",
\t"input_tp" : "-18.06",
\t"input_lra" : "0.00",
\t"input_thresh" : "-31.75",
\t"output_i" : "-16.05",
\t"output_tp" : "-12.31",
\t"output_lra" : "0.00",
\t"output_thresh" : "-26.05",
\t"normalization_type" : "linear",
\t"target_offset" : "0.05"
}
[out#0/null @ 0x7] video:0KiB audio:750KiB
"""


# ---- pure parsing and planning -------------------------------------------------


def test_parse_summary_uses_final_block_only():
    m = parse_ebur128_summary(SUMMARY)
    assert m == Measure(integrated=-21.8, true_peak=-18.1, lra=3.5)


def test_parse_summary_handles_silence():
    text = SUMMARY.replace("-21.8 LUFS\n    Threshold", "-inf LUFS\n    Threshold").replace(
        "-18.1 dBFS", "-inf dBFS"
    )
    m = parse_ebur128_summary(text)
    assert m.integrated == -math.inf and m.true_peak == -math.inf
    assert m.is_silent


def test_parse_summary_errors():
    with pytest.raises(LoudnessError, match="no ebur128 summary"):
        parse_ebur128_summary("nothing here")
    with pytest.raises(LoudnessError, match="true peak"):
        parse_ebur128_summary("Summary:\n  I:  -20.0 LUFS\n")
    with pytest.raises(LoudnessError, match="integrated"):
        parse_ebur128_summary("Summary:\n  True peak:\n    Peak: -1.0 dBFS\n")


def test_parse_summary_without_lra():
    text = "Summary:\n  I: -20.0 LUFS\n  True peak:\n    Peak: -3.0 dBFS\n"
    assert parse_ebur128_summary(text).lra is None


def test_parse_loudnorm_json():
    data = parse_loudnorm_json(LOUDNORM)
    assert data["input_i"] == -21.75
    assert data["target_offset"] == 0.05
    assert data["normalization_type"] == "linear"


def test_parse_loudnorm_json_errors():
    with pytest.raises(LoudnessError, match="no loudnorm JSON"):
        parse_loudnorm_json("no braces")
    with pytest.raises(LoudnessError, match="malformed"):
        parse_loudnorm_json("{ not json }")
    with pytest.raises(LoudnessError, match="lacks"):
        parse_loudnorm_json('{"input_i": "-20"}')


def test_plan_gain_limited_by_target():
    gain, why = plan_gain(Measure(-20.0, -12.0), -14.0, -1.0)
    assert (gain, why) == (6.0, "target")


def test_plan_gain_limited_by_peak():
    gain, why = plan_gain(Measure(-20.0, -4.0), -14.0, -1.0)
    assert (gain, why) == (3.0, "peak")


def test_plan_gain_headroom_and_cap():
    gain, why = plan_gain(Measure(-40.0, -30.0), -14.0, -1.0, headroom_db=0.5, max_gain_db=12.0)
    assert (gain, why) == (12.0, "cap")
    gain, why = plan_gain(Measure(-20.0, -4.0), -14.0, -1.0, headroom_db=0.5)
    assert (gain, why) == (2.5, "peak")


def test_plan_gain_can_lower():
    gain, why = plan_gain(Measure(-8.0, -0.5), -14.0, -1.0)
    assert gain == -6.0 and why == "target"


def test_plan_gain_silent_raises():
    with pytest.raises(LoudnessError):
        plan_gain(Measure(-math.inf, -math.inf))


def test_common_target_is_lowest_reachable():
    clips = [Measure(-20.0, -6.0), Measure(-18.0, -2.0), Measure(-30.0, -20.0)]
    # clip 2 can reach -18 + (-1 - -2) = -17; clip 1 -15; clip 3 -11; target -14 -> min is -17
    assert common_target(clips, -14.0, -1.0) == -17.0
    assert common_target(clips[:1], -14.0, -1.0) == -15.0
    assert common_target([Measure(-30.0, -25.0)], -14.0, -1.0) == -14.0


def test_common_target_skips_silent_and_rejects_empty():
    silent = Measure(-math.inf, -math.inf)
    assert common_target([silent, Measure(-30.0, -25.0)], -14.0, -1.0) == -14.0
    with pytest.raises(LoudnessError):
        common_target([silent])
    with pytest.raises(LoudnessError):
        common_target([])


def test_filters():
    assert volume_filter(-3.456) == "volume=-3.46dB"
    assert loudnorm_filter(-16, -1.5) == "loudnorm=I=-16:TP=-1.5:LRA=11"
    flt = loudnorm_filter(-16, -1.5, measured=parse_loudnorm_json(LOUDNORM))
    assert "measured_I=-21.75" in flt and flt.endswith("linear=true")
    assert loudnorm_filter(print_json=True).endswith("print_format=json")
    limiter = limiter_filter(-1.0)
    assert limiter.startswith("aresample=192000,alimiter=limit=0.891251")
    assert limiter.endswith("aresample=48000")
    assert limiter_filter(-1.0, oversample=1).startswith("alimiter=")
    assert "limit=0.062500" in limiter_filter(-60.0)
    with pytest.raises(ValueError):
        limiter_filter(-1.0, oversample=0)
    with pytest.raises(ValueError):
        limiter_filter(3.0)


# ---- tests that need ffmpeg ------------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _sine(path, volume_db=0.0, seconds=3, video=False):
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"aevalsrc=sin(2*PI*440*t):d={seconds}:s=48000"]
    if video:
        cmd += ["-f", "lavfi", "-i", f"color=c=black:s=64x64:d={seconds}:r=10"]
    cmd += ["-af", f"volume={volume_db}dB"]
    if video:
        cmd += ["-c:v", "mpeg4", "-c:a", "aac", "-shortest"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    return path


@needs_ffmpeg
def test_measure_matches_expected_level(tmp_path):
    src = _sine(tmp_path / "a.wav", -20.0)
    m = measure(src)
    assert -26.0 < m.integrated < -22.0
    assert m.true_peak == pytest.approx(-20.0, abs=0.5)
    assert m.lra is not None


@needs_ffmpeg
def test_measure_missing_file_and_no_audio(tmp_path):
    with pytest.raises(LoudnessError):
        measure(tmp_path / "missing.wav")
    video = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1:r=10",
         "-c:v", "mpeg4", str(video)],
        check=True,
    )
    with pytest.raises(LoudnessError):
        measure(video)


@needs_ffmpeg
def test_measure_loudnorm_first_pass(tmp_path):
    src = _sine(tmp_path / "a.wav", -10.0)
    stats = measure_loudnorm(src, -16.0, -1.5)
    assert stats["input_tp"] == pytest.approx(-10.0, abs=0.6)


@needs_ffmpeg
def test_apply_gain_changes_level(tmp_path):
    src = _sine(tmp_path / "a.wav", -20.0)
    dst = tmp_path / "b.wav"
    apply_gain(src, dst, 6.0)
    assert measure(dst).integrated - measure(src).integrated == pytest.approx(6.0, abs=0.3)


@needs_ffmpeg
def test_normalize_gain_mode_hits_target(tmp_path):
    src = _sine(tmp_path / "a.wav", -24.0, seconds=4)
    dst = tmp_path / "out" / "b.wav"
    result = normalize(src, dst, target_lufs=-16.0, true_peak=-1.0)
    assert result.reached_target
    assert result.after.integrated == pytest.approx(-16.0, abs=0.5)
    assert result.after.true_peak <= -1.0
    assert result.limited_by == "target"


@needs_ffmpeg
def test_normalize_respects_peak_ceiling(tmp_path):
    src = _sine(tmp_path / "a.wav", -3.0)
    dst = tmp_path / "b.wav"
    # a sine peaking at -3 dBFS sits near -6 LUFS; a -6 dBTP ceiling is below it, so the peak decides
    result = normalize(src, dst, target_lufs=-5.0, true_peak=-6.0)
    assert result.limited_by == "peak"
    assert result.after.true_peak <= -6.0


@needs_ffmpeg
def test_normalize_loudnorm_mode(tmp_path):
    src = _sine(tmp_path / "a.wav", -24.0, seconds=4)
    dst = tmp_path / "b.wav"
    result = normalize(src, dst, target_lufs=-16.0, true_peak=-1.5, mode="loudnorm")
    assert result.after.true_peak <= -1.5
    assert result.after.integrated == pytest.approx(-16.0, abs=1.0)


@needs_ffmpeg
def test_normalize_keeps_video_stream(tmp_path):
    src = _sine(tmp_path / "a.mp4", -24.0, seconds=3, video=True)
    dst = tmp_path / "b.mp4"
    result = normalize(src, dst, target_lufs=-16.0, true_peak=-1.0)
    probe = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(dst)], capture_output=True, text=True).stderr
    assert "Video:" in probe and "Audio:" in probe
    assert result.after.true_peak <= -1.0


@needs_ffmpeg
def test_normalize_refuses_silence_and_same_path(tmp_path):
    silent = tmp_path / "s.wav"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=d=2", str(silent)], check=True
    )
    with pytest.raises(LoudnessError, match="silent"):
        normalize(silent, tmp_path / "o.wav")
    with pytest.raises(LoudnessError, match="differ"):
        normalize(silent, silent)


def test_normalize_rejects_bad_mode(tmp_path):
    with pytest.raises(ValueError):
        normalize(tmp_path / "a.wav", tmp_path / "b.wav", mode="nope")  # type: ignore[arg-type]


def test_measure_without_ffmpeg(monkeypatch, tmp_path):
    from uppercut_ffmpeg_loudness import core

    monkeypatch.setattr(core.shutil, "which", lambda name: None)
    with pytest.raises(core.FfmpegNotFoundError):
        measure(tmp_path / "x.wav")


@needs_ffmpeg
def test_cli_measure_json(tmp_path, capsys):
    src = _sine(tmp_path / "a.wav", -20.0)
    code = cli_main([str(src), "--json", "--target", "-14"])
    out = json.loads(capsys.readouterr().out)
    assert out[0]["file"] == str(src)
    assert code == 1  # far from the target


def test_cli_reports_missing_ffmpeg(monkeypatch, tmp_path, capsys):
    from uppercut_ffmpeg_loudness import core

    monkeypatch.setattr(core.shutil, "which", lambda name: None)
    assert cli_main([str(tmp_path / "x.wav")]) == 2
    assert "ffmpeg" in capsys.readouterr().err
