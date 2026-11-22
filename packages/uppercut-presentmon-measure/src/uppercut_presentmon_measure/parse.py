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
# Milliseconds between two frames reaching the display; 0 or NA for a frame that was never displayed.
DISPLAY_CHANGE_COLUMNS = ("msBetweenDisplayChange",)
TIME_COLUMNS = ("TimeInSeconds",)
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
    """Return one dict per presented frame.

    Each frame is ``{"frame_time_ms": float, "dropped": bool, "display_ms": float | None,
    "time_s": float | None}``. ``display_ms`` is the ``msBetweenDisplayChange`` column (when the frame
    reached the display, as opposed to when it was presented) and ``time_s`` is ``TimeInSeconds``;
    both are ``None`` when the CSV has no such column or the value is blank or ``NA``.

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
    display_col = _column(header, DISPLAY_CHANGE_COLUMNS)
    time_col_s = _column(header, TIME_COLUMNS)
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
        frames.append({
            "frame_time_ms": value,
            "dropped": dropped,
            "display_ms": _optional_number(row, display_col, line),
            "time_s": _optional_number(row, time_col_s, line),
        })
    return frames


def _optional_number(row: Mapping, column: str | None, line: int) -> float | None:
    if column is None:
        return None
    raw = (row.get(column) or "").strip()
    if not raw or raw.upper() == "NA":
        return None
    try:
        return float(raw)
    except ValueError as error:
        raise FrameParseError(f"line {line}: {raw!r} is not a number") from error


PERCENTILE_METHODS = ("linear", "index")


def percentile(values: Sequence[float], pct: float, method: str = "linear") -> float:
    """Percentile of ``values``, ``pct`` in 0..100. Raises ValueError on an empty list.

    ``method="linear"`` (default) interpolates between the two nearest ranks. ``method="index"``
    takes ``sorted(values)[min(n - 1, int(pct / 100 * n))]``, a plain index with no interpolation,
    which is what many hand-written scripts do; use it to keep numbers equal to such a script's.
    """
    if not values:
        raise ValueError("percentile of an empty list")
    if not 0 <= pct <= 100:
        raise ValueError("pct must be between 0 and 100")
    if method not in PERCENTILE_METHODS:
        raise ValueError(f"method must be one of {', '.join(PERCENTILE_METHODS)}")
    ordered = sorted(values)
    if method == "index":
        return ordered[min(len(ordered) - 1, int(pct / 100.0 * len(ordered)))]
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


def summarize_displayed(
    frames: Sequence[Mapping], refresh_hz: float, percentile_method: str = "linear"
) -> dict:
    """Summarize when frames reached the display, from the ``display_ms`` of parsed frames.

    ``summarize_frames`` measures when frames were presented; this measures the cadence the display
    actually showed. Frames with no display interval (zero, ``NA`` or no such column) were never
    displayed and are left out of the interval statistics but still count in ``presents``. A frame
    is late when its interval is more than 1.5 display periods (``1000 / refresh_hz`` ms).

    Returns ``{"presents", "displayed"}`` alone when no frame was displayed. Otherwise also
    ``periodMs``, ``refreshHz``, ``displayIntervalMs`` (min, mean, p50, p90, p95, p99, max),
    ``lateFrames``, ``latePct``, and, when the frames carry ``time_s``, ``durationSeconds`` (first to
    last frame) and ``displayedFps``. ``percentile_method`` is passed to ``percentile``.
    """
    if not refresh_hz or refresh_hz <= 0:
        raise ValueError("refresh_hz must be positive")
    shown = sorted(
        float(f["display_ms"]) for f in frames
        if f.get("display_ms") is not None and float(f["display_ms"]) > 0
    )
    result: dict = {"presents": len(frames), "displayed": len(shown)}
    if not shown:
        return result
    period = 1000.0 / refresh_hz
    late = sum(1 for t in shown if t > 1.5 * period)
    stamps = [float(f["time_s"]) for f in frames if f.get("time_s") is not None]
    span = stamps[-1] - stamps[0] if len(stamps) > 1 else 0.0

    def at(pct: float) -> float:
        return round(percentile(shown, pct, percentile_method), 3)

    result.update({
        "refreshHz": refresh_hz,
        "periodMs": round(period, 3),
        "displayIntervalMs": {
            "min": round(shown[0], 3),
            "mean": round(statistics.fmean(shown), 3),
            "p50": at(50), "p90": at(90), "p95": at(95), "p99": at(99),
            "max": round(shown[-1], 3),
        },
        "lateFrames": late,
        "latePct": round(100.0 * late / len(shown), 2),
    })
    if span > 0:
        result["durationSeconds"] = round(span, 3)
        result["displayedFps"] = round(len(shown) / span, 2)
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
