"""Check and fix cut times against a beat grid."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from .grid import beat_times, with_half_beats


@dataclass(frozen=True)
class CutResult:
    cut: float
    nearest: float
    delta_ms: float  # cut minus nearest beat
    residual_ms: float  # delta after the global offset is removed
    ok: bool


@dataclass(frozen=True)
class AlignReport:
    results: list[CutResult]
    offset_ms: float
    tolerance_ms: float

    @property
    def misses(self) -> int:
        return sum(not r.ok for r in self.results)

    @property
    def all_ok(self) -> bool:
        return self.misses == 0


def cut_times(cuts: Sequence[Any] | Mapping[str, Any]) -> list[float]:
    """Cut times from a list of numbers or `{"t": ...}` dicts, or a dict with a `cuts` list."""
    items = cuts["cuts"] if isinstance(cuts, Mapping) else cuts
    return [float(c["t"]) if isinstance(c, Mapping) else float(c) for c in items]


def align(
    cuts: Sequence[Any] | Mapping[str, Any],
    beats: Sequence[Any] | Mapping[str, Any],
    tolerance_ms: float = 17.0,
    half: bool = False,
    auto_offset: bool = False,
) -> AlignReport:
    """Measure how far each cut is from the nearest beat.

    `tolerance_ms` is how far a cut may sit from a beat and still pass: 17 ms is one frame at
    60 fps, 34 ms one frame at 30 fps. With `half`, half-beats count as targets too. With
    `auto_offset`, the median cut-to-beat delta is removed first, for checking a delivered
    file whose detected beats lag the true transients by a constant.
    """
    if tolerance_ms < 0:
        raise ValueError("tolerance_ms must not be negative")
    cs = cut_times(cuts)
    bs = beat_times(beats)
    if not bs:
        raise ValueError("there are no beats to align to")
    if half:
        bs = with_half_beats(bs)
    arr = np.asarray(bs)
    nearest = [float(arr[int(np.argmin(np.abs(arr - c)))]) for c in cs]
    deltas = [(c - n) * 1000.0 for c, n in zip(cs, nearest)]
    offset = float(np.median(deltas)) if auto_offset and deltas else 0.0
    results = [
        CutResult(c, n, d, d - offset, abs(d - offset) <= tolerance_ms) for c, n, d in zip(cs, nearest, deltas)
    ]
    return AlignReport(results, offset, tolerance_ms)


def snap_cuts(
    cuts: Sequence[Any] | Mapping[str, Any],
    beats: Sequence[Any] | Mapping[str, Any],
    half: bool = False,
    reach: float | None = None,
) -> list[float]:
    """Move each cut to its nearest beat. Cuts farther than `reach` seconds from any beat are kept."""
    cs = cut_times(cuts)
    bs = beat_times(beats)
    if not bs:
        raise ValueError("there are no beats to snap to")
    if half:
        bs = with_half_beats(bs)
    arr = np.asarray(bs)
    out = []
    for c in cs:
        nearest = float(arr[int(np.argmin(np.abs(arr - c)))])
        out.append(nearest if reach is None or abs(nearest - c) <= reach else c)
    return out
