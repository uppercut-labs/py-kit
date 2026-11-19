"""The beat grid data model: a plain dict that round-trips through JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

Grid = dict[str, Any]


def make_grid(
    bpm: float,
    duration: float,
    offset: float = 0.0,
    fps: float = 60.0,
    beats_per_bar: int = 4,
) -> Grid:
    """A planned grid for a known tempo.

    Returns `{bpm, seconds_per_beat, fps, frames_per_beat, beats_per_bar, duration, beats,
    half_beats}`. Each beat is `{i, t, bar, beat, frame}`: its index, time in seconds, 1-based
    bar and beat-in-bar, and the video frame it falls on at `fps`.
    """
    if bpm <= 0:
        raise ValueError("bpm must be positive")
    if fps <= 0:
        raise ValueError("fps must be positive")
    if beats_per_bar < 1:
        raise ValueError("beats_per_bar must be at least 1")
    if duration < 0:
        raise ValueError("duration must not be negative")
    spb = 60.0 / bpm
    count = int(np.floor((duration - offset) / spb + 1e-9)) + 1 if duration >= offset else 0
    beats = []
    for i in range(count):
        t = offset + i * spb
        beats.append(
            {
                "i": i,
                "t": round(t, 4),
                "bar": i // beats_per_bar + 1,
                "beat": i % beats_per_bar + 1,
                "frame": frame_index(t, fps),
            }
        )
    return {
        "bpm": bpm,
        "seconds_per_beat": round(spb, 6),
        "fps": fps,
        "frames_per_beat": round(spb * fps, 4),
        "beats_per_bar": beats_per_bar,
        "duration": duration,
        "beats": beats,
        "half_beats": [round(offset + (k + 0.5) * spb, 4) for k in range(count)],
    }


def frame_index(t: float, fps: float) -> int:
    """The nearest video frame to a time in seconds."""
    return int(round(t * fps))


def beat_times(source: Mapping[str, Any] | Iterable[Any]) -> list[float]:
    """Beat times in seconds from a grid dict (`beats` list of dicts or numbers) or a plain list."""
    items = source["beats"] if isinstance(source, Mapping) else source
    return [float(b["t"]) if isinstance(b, Mapping) else float(b) for b in items]


def with_half_beats(beats: Sequence[float]) -> list[float]:
    """Beat times plus the midpoint between each pair, sorted."""
    b = list(beats)
    return sorted(b + [(p + q) / 2 for p, q in zip(b, b[1:])])


def snap_to_grid(t: float, beats: Sequence[float], reach: float | None = None) -> float | None:
    """The beat time nearest to `t`, or None when none lies within `reach` seconds.

    With `reach=None` the nearest beat is always returned. Returns None for an empty grid.
    """
    if len(beats) == 0:
        return None
    arr = np.asarray(beats, dtype=float)
    nearest = float(arr[int(np.argmin(np.abs(arr - t)))])
    if reach is not None and abs(nearest - t) > reach:
        return None
    return nearest


def save_grid(grid: Mapping[str, Any], path: str | Path) -> None:
    Path(path).write_text(json.dumps(grid, indent=1) + "\n", encoding="utf-8")


def load_grid(path: str | Path) -> Grid:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "beats" not in data:
        raise ValueError(f"{path} is not a beat grid: no 'beats' list")
    return data
