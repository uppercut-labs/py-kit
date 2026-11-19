"""Frame grabbing for video contact sheets. Needs ffmpeg and ffprobe on PATH."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from .sheet import Tile


def ffmpeg_available() -> bool:
    """True when both ffmpeg and ffprobe can be found on PATH."""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def parse_time(text: str | float | int) -> float:
    """Parse seconds (``12.5``) or ``mm:ss`` / ``hh:mm:ss`` into seconds."""
    total = 0.0
    for part in str(text).strip().split(":"):
        total = total * 60 + float(part)
    return total


def format_time(t: float) -> str:
    """Format seconds as ``m:ss`` or ``m:ss.ss`` when there is a fractional part."""
    m, s = divmod(t, 60)
    return f"{int(m)}:{s:05.2f}" if round(s, 2) % 1 else f"{int(m)}:{int(round(s)):02d}"


def frame_times(
    duration: float,
    *,
    every: float | None = None,
    count: int | None = None,
    start: float = 0.0,
    end: float | None = None,
) -> list[float]:
    """Pick timestamps: one every ``every`` seconds, or ``count`` evenly spaced.

    Exactly one of ``every`` and ``count`` is required. Times stay inside
    ``[start, end]`` (``end`` defaults to the duration).
    """
    if (every is None) == (count is None):
        raise ValueError("give exactly one of every and count")
    end = duration if end is None else min(end, duration)
    if end <= start:
        raise ValueError("end must be after start")
    if every is not None:
        if every <= 0:
            raise ValueError("every must be positive")
        n = int((end - start) / every) + 1
        return [start + i * every for i in range(n)]
    if count < 1:
        raise ValueError("count must be at least 1")
    return [start + (end - start) * (i + 0.5) / count for i in range(count)]


def probe_duration(video: str | Path) -> float:
    """Duration in seconds of the first video stream, falling back to the container."""
    if not ffmpeg_available():
        raise RuntimeError("ffprobe was not found on PATH")
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=duration:format=duration", "-of", "json", str(video)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    streams = data.get("streams") or [{}]
    value = streams[0].get("duration") or data.get("format", {}).get("duration")
    if value is None:
        raise RuntimeError(f"could not read the duration of {video}")
    return float(value)


def grab_frame(video: str | Path, t: float, width: int | None = None, *, retries: int = 3, step: float = 1 / 30) -> Image.Image:
    """Decode one frame at ``t`` seconds, optionally scaled to ``width`` px.

    A seek near the end can land past the last frame, so the time is stepped
    back by ``step`` seconds up to ``retries`` times before giving up.
    """
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg was not found on PATH")
    scale = ["-vf", f"scale={width}:-2"] if width else []
    last = ""
    for k in range(retries + 1):
        tt = max(t - k * step, 0.0)
        r = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{tt:.4f}", "-i", str(video),
             "-frames:v", "1", *scale, "-f", "image2pipe", "-c:v", "png", "-"],
            capture_output=True,
        )
        if r.returncode == 0 and r.stdout:
            return Image.open(io.BytesIO(r.stdout)).convert("RGB")
        last = r.stderr.decode(errors="replace").strip()[-300:]
    raise RuntimeError(f"could not grab a frame near {t:.3f}s: {last}")


def video_tiles(
    video: str | Path,
    times: list[float],
    *,
    width: int = 480,
) -> list[Tile]:
    """Grab a frame at each time and label it with the timestamp."""
    return [Tile(grab_frame(video, t, width), format_time(t)) for t in times]
