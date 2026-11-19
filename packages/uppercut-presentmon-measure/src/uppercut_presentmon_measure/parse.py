"""Pure functions: parse a PresentMon CSV and summarize frame timing. No I/O, no platform checks."""
from __future__ import annotations

import csv
import io
import math
import statistics
from typing import Iterable, Mapping, Sequence

# Column names differ between PresentMon versions; the first one present wins (case-insensitive).
FRAME_TIME_COLUMNS = ("FrameTime", "msBetweenPresents")
DROPPED_COLUMNS = ("Dropped", "WasDropped")
_TRUE = {"1", "true", "yes"}


class FrameParseError(ValueError):
    """The CSV has no usable frame-time column or a value that is not a number."""


def _column(header: Sequence[str], names: Iterable[str]) -> str | None:
    lowered = {h.strip().lower(): h for h in header}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def parse_presentmon_csv(text: str, frame_time_column: str | None = None) -> list[dict]:
    """Return one dict per presented frame: ``{"frame_time_ms": float, "dropped": bool}``.

    An empty capture (no header or no rows) returns an empty list. A header with no frame-time
    column raises FrameParseError, as does a value that is not a number.
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    header = reader.fieldnames or []
    if not header:
        return []
    time_col = _column(header, [frame_time_column] if frame_time_column else FRAME_TIME_COLUMNS)
    if time_col is None:
        raise FrameParseError(f"no frame-time column found; columns are {list(header)}")
    dropped_col = _column(header, DROPPED_COLUMNS)
    frames: list[dict] = []
    for line, row in enumerate(reader, start=2):
        raw = (row.get(time_col) or "").strip()
        if not raw or raw.upper() == "NA":
            continue
        try:
            value = float(raw)
        except ValueError as error:
            raise FrameParseError(f"line {line}: {raw!r} is not a number") from error
        dropped = dropped_col is not None and (row.get(dropped_col) or "").strip().lower() in _TRUE
        frames.append({"frame_time_ms": value, "dropped": dropped})
    return frames


def percentile(values: Sequence[float], pct: float) -> float:
    """Linear-interpolated percentile, ``pct`` in 0..100. Raises ValueError on an empty list."""
    if not values:
        raise ValueError("percentile of an empty list")
    if not 0 <= pct <= 100:
        raise ValueError("pct must be between 0 and 100")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct / 100.0
    low, high = math.floor(rank), math.ceil(rank)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def summarize_frames(frames: Sequence[Mapping], refresh_hz: float | None = None) -> dict:
    """Summarize parsed frames: percentiles, average fps, dropped and long frames.

    ``refresh_hz`` adds ``missedRefreshFrames``: frames longer than 1.5 refresh intervals.
    An empty capture returns ``{"frames": 0}`` rather than raising, since an empty capture is a
    finding about the run.
    """
    if not frames:
        return {"frames": 0}
    times = [float(f["frame_time_ms"]) for f in frames]
    total_ms = sum(times)
    dropped = sum(1 for f in frames if f.get("dropped"))
    result: dict = {
        "frames": len(times),
        "durationSeconds": round(total_ms / 1000.0, 3),
        "averageFps": round(len(times) / (total_ms / 1000.0), 2) if total_ms > 0 else None,
        "frameTimeMs": {
            "min": round(min(times), 3),
            "mean": round(statistics.fmean(times), 3),
            "p50": round(percentile(times, 50), 3),
            "p90": round(percentile(times, 90), 3),
            "p95": round(percentile(times, 95), 3),
            "p99": round(percentile(times, 99), 3),
            "max": round(max(times), 3),
        },
        "droppedFrames": dropped,
        "droppedPercent": round(100.0 * dropped / len(times), 2),
    }
    if refresh_hz:
        limit = 1.5 * 1000.0 / refresh_hz
        result["refreshHz"] = refresh_hz
        result["missedRefreshFrames"] = sum(1 for t in times if t > limit)
    return result


def summarize_runs(values_ms: Sequence[float | None]) -> dict:
    """Summarize repeated startup runs. ``None`` marks a run that never reported."""
    good = [v for v in values_ms if v is not None]
    return {
        "runsMs": list(values_ms),
        "failedRuns": len(values_ms) - len(good),
        "minMs": min(good) if good else None,
        "medianMs": statistics.median(good) if good else None,
        "maxMs": max(good) if good else None,
    }


def summarize_startup_marks(marks: Mapping[str, float], launch_epoch_ms: float) -> dict:
    """Convert marks (name -> epoch milliseconds) to milliseconds since launch.

    Marks that are not numbers or that are earlier than the launch time are dropped.
    """
    return {
        name: round(float(at) - launch_epoch_ms, 1)
        for name, at in marks.items()
        if isinstance(at, (int, float)) and not isinstance(at, bool) and float(at) >= launch_epoch_ms
    }


def parse_marks(text: str) -> dict[str, float]:
    """Read a marks file written by the app: ``{"marks": {"name": epoch_ms}}`` or a flat ``{"name": epoch_ms}``.

    Non-numeric entries are ignored. Text that is not a JSON object returns an empty dict, so a
    half-written file reads as "no marks yet".
    """
    import json

    try:
        data = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    inner = data.get("marks")
    if isinstance(inner, dict):
        data = inner
    return {
        k: float(v)
        for k, v in data.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
