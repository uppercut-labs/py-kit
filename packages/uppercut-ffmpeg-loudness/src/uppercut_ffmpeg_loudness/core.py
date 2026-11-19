"""ffmpeg loudness measurement and gain helpers.

Everything that parses ffmpeg output or builds a filter string is a pure function.
Only `measure`, `measure_loudnorm`, `apply_gain` and `normalize` run ffmpeg.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

DEFAULT_TARGET_LUFS = -14.0
DEFAULT_TRUE_PEAK_DBTP = -1.0
REACHED_TOLERANCE_LU = 0.5

_NUMBER = r"(-?\d+(?:\.\d+)?|-inf|inf)"
_AUDIO_ONLY_SUFFIXES = {".wav", ".flac", ".mp3", ".ogg", ".opus", ".aiff", ".aif"}
_CODEC_BY_SUFFIX = {
    ".wav": "pcm_s24le",
    ".aiff": "pcm_s24le",
    ".aif": "pcm_s24le",
    ".flac": "flac",
    ".mp3": "libmp3lame",
    ".ogg": "libvorbis",
    ".opus": "libopus",
}


class LoudnessError(Exception):
    """Measurement or normalization failed; the message says why."""


class FfmpegNotFoundError(LoudnessError):
    """ffmpeg is not on PATH."""


@dataclass(frozen=True)
class Measure:
    """Result of an EBU R128 measurement. Silence reads as -inf."""

    integrated: float
    true_peak: float
    lra: float | None = None

    @property
    def is_silent(self) -> bool:
        return self.true_peak == -math.inf or self.integrated == -math.inf


@dataclass(frozen=True)
class NormalizeResult:
    """What `normalize` did: the gain applied, what limited it, and both measurements."""

    gain_db: float
    limited_by: str
    before: Measure
    after: Measure
    attempts: int
    reached_target: bool


def _to_float(text: str) -> float:
    if text == "-inf":
        return -math.inf
    if text == "inf":
        return math.inf
    return float(text)


def parse_ebur128_summary(log: str) -> Measure:
    """Read the final `Summary:` block that ffmpeg's ebur128 filter prints.

    Running per-100 ms lines before the summary are ignored. Raises LoudnessError
    when there is no summary (for example, the input had no audio stream).
    """
    index = log.rfind("Summary:")
    if index < 0:
        raise LoudnessError("ffmpeg printed no ebur128 summary")
    summary = log[index:]
    integrated = re.search(r"\bI:\s+" + _NUMBER + r"\s+LUFS", summary)
    if integrated is None:
        raise LoudnessError("ebur128 summary has no integrated loudness")
    peak = re.search(r"True peak:\s*Peak:\s+" + _NUMBER + r"\s+dBFS", summary)
    if peak is None:
        raise LoudnessError("ebur128 summary has no true peak; run with peak=true")
    lra = re.search(r"\bLRA:\s+" + _NUMBER + r"\s+LU", summary)
    return Measure(
        integrated=_to_float(integrated.group(1)),
        true_peak=_to_float(peak.group(1)),
        lra=_to_float(lra.group(1)) if lra else None,
    )


def parse_loudnorm_json(log: str) -> dict[str, float | str]:
    """Read the JSON block printed by `loudnorm=print_format=json`.

    Numeric fields come back as floats (`input_i`, `input_tp`, `input_lra`,
    `input_thresh`, `output_*`, `target_offset`); `normalization_type` stays a string.
    """
    end = log.rfind("}")
    start = log.rfind("{", 0, end + 1) if end >= 0 else -1
    if start < 0 or end < 0:
        raise LoudnessError("ffmpeg printed no loudnorm JSON")
    try:
        raw = json.loads(log[start : end + 1])
    except json.JSONDecodeError as error:
        raise LoudnessError(f"loudnorm JSON is malformed: {error}") from error
    out: dict[str, float | str] = {}
    for key, value in raw.items():
        out[key] = str(value) if key == "normalization_type" else _to_float(str(value))
    for needed in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        if needed not in out:
            raise LoudnessError(f"loudnorm JSON lacks {needed}")
    return out


def plan_gain(
    before: Measure,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    true_peak_ceiling: float = DEFAULT_TRUE_PEAK_DBTP,
    headroom_db: float = 0.0,
    max_gain_db: float | None = None,
) -> tuple[float, str]:
    """One linear gain in dB and what limited it: "target", "peak" or "cap".

    The gain is the smallest of: what reaches the target loudness, what keeps the
    true peak under the ceiling minus `headroom_db`, and `max_gain_db` when given.
    Raises LoudnessError for silent input, which has no meaningful gain.
    """
    if before.is_silent:
        raise LoudnessError("input is silent; there is no gain to plan")
    limits: dict[str, float] = {
        "target": target_lufs - before.integrated,
        "peak": true_peak_ceiling - headroom_db - before.true_peak,
    }
    if max_gain_db is not None:
        limits["cap"] = max_gain_db
    limited_by = min(limits, key=lambda name: limits[name])
    return round(limits[limited_by], 2), limited_by


def common_target(
    measures: Sequence[Measure],
    target_lufs: float = DEFAULT_TARGET_LUFS,
    true_peak_ceiling: float = DEFAULT_TRUE_PEAK_DBTP,
) -> float:
    """The loudness a batch can share without any clip passing the peak ceiling.

    Returns the lowest of `target_lufs` and, for each non-silent clip, the loudness it
    would have after being raised as far as its true peak allows. Clips are never
    pushed past their peaks; the batch is lowered to the value returned.
    """
    reachable = [
        m.integrated + (true_peak_ceiling - m.true_peak) for m in measures if not m.is_silent
    ]
    if not reachable:
        raise LoudnessError("no measurable clips")
    return round(min([target_lufs, *reachable]), 2)


def volume_filter(gain_db: float) -> str:
    return f"volume={gain_db:.2f}dB"


def limiter_filter(limit_dbfs: float, sample_rate: int = 48000, oversample: int = 4) -> str:
    """An `alimiter` chain that limits peaks while working at a higher sample rate.

    alimiter takes a linear limit, so the dB value is converted (alimiter accepts
    0.0625 to 1). The audio is resampled up by `oversample`, limited, and returned to
    `sample_rate`.
    """
    if oversample < 1:
        raise ValueError("oversample must be at least 1")
    if limit_dbfs > 0:
        raise ValueError("limit_dbfs must be 0 or below")
    limit = max(10 ** (limit_dbfs / 20), 0.0625)
    chain = []
    if oversample > 1:
        chain.append(f"aresample={sample_rate * oversample}")
    chain.append(f"alimiter=limit={limit:.6f}:level=disabled:attack=5:release=50")
    if oversample > 1:
        chain.append(f"aresample={sample_rate}")
    return ",".join(chain)


def loudnorm_filter(
    target_lufs: float = DEFAULT_TARGET_LUFS,
    true_peak: float = DEFAULT_TRUE_PEAK_DBTP,
    lra: float = 11.0,
    measured: dict[str, float | str] | None = None,
    print_json: bool = False,
) -> str:
    """Build a `loudnorm` filter. With `measured` (from parse_loudnorm_json) it is the
    second, linear pass; without it, a first pass or a dynamic single pass."""
    parts = [f"I={target_lufs:g}", f"TP={true_peak:g}", f"LRA={lra:g}"]
    if measured is not None:
        parts += [
            f"measured_I={measured['input_i']}",
            f"measured_TP={measured['input_tp']}",
            f"measured_LRA={measured['input_lra']}",
            f"measured_thresh={measured['input_thresh']}",
            f"offset={measured['target_offset']}",
            "linear=true",
        ]
    if print_json:
        parts.append("print_format=json")
    return "loudnorm=" + ":".join(parts)


def _require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise FfmpegNotFoundError("ffmpeg was not found on PATH")
    return path


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def measure(path: str | Path, audio_stream: int = 0) -> Measure:
    """Integrated loudness, true peak and loudness range of a file's audio (ebur128)."""
    ffmpeg = _require_ffmpeg()
    done = _run(
        [ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-i", str(path)]
        + ["-map", f"0:a:{audio_stream}", "-af", "ebur128=peak=true", "-f", "null", "-"]
    )
    if done.returncode != 0:
        raise LoudnessError(f"ffmpeg could not measure {path}: {done.stderr.strip()[-400:]}")
    return parse_ebur128_summary(done.stderr)


def measure_loudnorm(
    path: str | Path,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    true_peak: float = DEFAULT_TRUE_PEAK_DBTP,
    lra: float = 11.0,
) -> dict[str, float | str]:
    """First loudnorm pass: the `input_*` measurements and `target_offset`."""
    ffmpeg = _require_ffmpeg()
    flt = loudnorm_filter(target_lufs, true_peak, lra, print_json=True)
    done = _run(
        [ffmpeg, "-nostdin", "-hide_banner", "-i", str(path), "-vn", "-af", flt, "-f", "null", "-"]
    )
    if done.returncode != 0:
        raise LoudnessError(f"ffmpeg loudnorm failed on {path}: {done.stderr.strip()[-400:]}")
    return parse_loudnorm_json(done.stderr)


def _output_args(dst: Path, copy_video: bool, codec: str | None, bitrate: int, rate: int) -> list[str]:
    suffix = dst.suffix.lower()
    chosen = codec or _CODEC_BY_SUFFIX.get(suffix, "aac")
    if suffix in _AUDIO_ONLY_SUFFIXES or suffix in {".m4a", ".aac"}:
        args = ["-map", "0:a:0", "-vn"]
    elif copy_video:
        args = ["-map", "0:v?", "-map", "0:a:0", "-c:v", "copy"]
    else:
        args = ["-map", "0:a:0", "-vn"]
    args += ["-c:a", chosen]
    if chosen in {"aac", "libmp3lame", "libopus", "libvorbis"}:
        args += ["-b:a", str(bitrate)]
    args += ["-ar", str(rate)]
    if suffix in {".mp4", ".m4a", ".mov"}:
        args += ["-movflags", "+faststart"]
    return args


def _encode(
    src: Path,
    dst: Path,
    chain: str,
    copy_video: bool,
    codec: str | None,
    bitrate: int,
    rate: int,
) -> None:
    ffmpeg = _require_ffmpeg()
    args = [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(src), "-af", chain]
    args += _output_args(dst, copy_video, codec, bitrate, rate) + [str(dst)]
    done = _run(args)
    if done.returncode != 0:
        dst.unlink(missing_ok=True)
        raise LoudnessError(f"ffmpeg failed: {done.stderr.strip()[-400:]}")


def apply_gain(
    src: str | Path,
    dst: str | Path,
    gain_db: float,
    *,
    extra_filters: Sequence[str] = (),
    copy_video: bool = True,
    codec: str | None = None,
    bitrate: int = 192_000,
    rate: int = 48_000,
) -> None:
    """Write `dst` with `gain_db` applied to the first audio stream.

    Video is copied unchanged when `copy_video` is true and `dst` is a video container.
    The audio codec follows the output suffix (AAC for mp4/mov, PCM for wav) unless
    `codec` is given. `extra_filters` run after the gain, for example a limiter.
    """
    chain = ",".join([volume_filter(gain_db), *extra_filters])
    _encode(Path(src), Path(dst), chain, copy_video, codec, bitrate, rate)


def normalize(
    src: str | Path,
    dst: str | Path,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    true_peak: float = DEFAULT_TRUE_PEAK_DBTP,
    *,
    mode: Literal["gain", "loudnorm"] = "gain",
    headroom_db: float = 0.5,
    max_gain_db: float | None = 12.0,
    retries: int = 3,
    lra: float = 11.0,
    copy_video: bool = True,
    codec: str | None = None,
    bitrate: int = 192_000,
    rate: int = 48_000,
) -> NormalizeResult:
    """Bring `src` to `target_lufs` while keeping the true peak at or under `true_peak`.

    mode="gain": one linear gain from `plan_gain`. After encoding, the output is measured;
    if its true peak is over the ceiling, the gain drops by the excess plus 0.1 dB and the
    file is encoded again, up to `retries` times, then LoudnessError.

    mode="loudnorm": two-pass ffmpeg loudnorm (measure, then linear correction), then the
    same measure-and-trim loop for any remaining peak overshoot.

    Silent input is refused with LoudnessError.
    """
    if mode not in ("gain", "loudnorm"):
        raise ValueError("mode must be 'gain' or 'loudnorm'")
    src, dst = Path(src), Path(dst)
    if src.resolve() == dst.resolve():
        raise LoudnessError("dst must differ from src")
    before = measure(src)
    if before.is_silent:
        raise LoudnessError(f"{src} is silent; nothing to normalize")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shared = {"copy_video": copy_video, "codec": codec, "bitrate": bitrate, "rate": rate}
    gain = 0.0
    limited_by = "target"
    attempts = 0
    if mode == "loudnorm":
        stats = measure_loudnorm(src, target_lufs, true_peak, lra)
        chain = loudnorm_filter(target_lufs, true_peak, lra, measured=stats)
        _encode(src, dst, chain, **shared)  # type: ignore[arg-type]
        attempts = 1
        after = measure(dst)
        trim_file = dst.with_name(dst.stem + ".trim" + dst.suffix)
        while after.true_peak > true_peak and attempts <= retries:
            excess = round(after.true_peak - true_peak + 0.1, 2)
            apply_gain(dst, trim_file, -excess, **shared)  # type: ignore[arg-type]
            trim_file.replace(dst)
            gain -= excess
            limited_by = "peak"
            attempts += 1
            after = measure(dst)
        gain = round(gain, 2)
    else:
        gain, limited_by = plan_gain(before, target_lufs, true_peak, headroom_db, max_gain_db)
        after = before
        for attempts in range(1, retries + 1):
            apply_gain(src, dst, gain, **shared)  # type: ignore[arg-type]
            after = measure(dst)
            if after.true_peak <= true_peak:
                break
            gain = round(gain - (after.true_peak - true_peak) - 0.1, 2)
            limited_by = "peak"
    if after.true_peak > true_peak:
        dst.unlink(missing_ok=True)
        raise LoudnessError(
            f"true peak stayed over {true_peak} dBTP ({after.true_peak}) after {attempts} encodes"
        )
    reached = abs(after.integrated - target_lufs) <= REACHED_TOLERANCE_LU
    return NormalizeResult(gain, limited_by, before, after, attempts, reached)
