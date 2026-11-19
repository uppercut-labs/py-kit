"""A synthesized music bed at an exact tempo, with the beat grid it was built on.

Because the grid is known, cuts can land on beats with no detection error. The bed is
deterministic (same arguments, same samples). It is steady and unobtrusive under a voice,
punctuated where the sections change. Only NumPy is needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .audio import write_wav24
from .grid import frame_index, save_grid

SAMPLE_RATE = 48000
NOTES = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
SCALES = {"minor": [0, 2, 3, 5, 7, 8, 10], "major": [0, 2, 4, 5, 7, 9, 11], "dorian": [0, 2, 3, 5, 7, 9, 10]}
PROGRESSIONS = {"minor": [0, 5, 3, 6], "major": [0, 4, 5, 3], "dorian": [0, 3, 0, 6]}  # scale degrees, one chord per bar
STYLES = ("pulse", "lofi", "drive")
SECTION_NAMES = ("intro", "build", "groove", "drop", "break", "outro", "hold")


@dataclass
class Bed:
    """A rendered bed: per-instrument stems, the mix, the grid and the sample rate.

    Stems and mix are mono float arrays already scaled so the mix peaks at -6 dBFS.
    """

    stems: dict[str, np.ndarray]
    mix: np.ndarray
    grid: dict[str, Any]
    rate: int = SAMPLE_RATE


class _Noise:
    """Seeded white noise from the PCG64 bit stream (Box-Muller on raw integers)."""

    def __init__(self, seed: int) -> None:
        self._bits = np.random.PCG64(int(seed) & ((1 << 64) - 1))

    def white(self, n: int) -> np.ndarray:
        pairs = (n + 1) // 2
        raw = self._bits.random_raw(2 * pairs)
        u = (raw >> np.uint64(11)).astype(np.float64) * (1.0 / 9007199254740992.0)
        r = np.sqrt(-2.0 * np.log(1.0 - u[:pairs]))
        out = np.empty(2 * pairs)
        out[0::2] = r * np.cos(2 * np.pi * u[pairs:])
        out[1::2] = r * np.sin(2 * np.pi * u[pairs:])
        return out[:n]


def parse_sections(text: str) -> list[tuple[str, int]]:
    """`"intro:2,build:4"` into `[("intro", 2), ("build", 4)]`."""
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, bars = part.partition(":")
        if not name or not bars.isdigit() or int(bars) < 1:
            raise ValueError(f"bad section {part!r}; expected name:bars with bars of at least 1")
        out.append((name, int(bars)))
    if not out:
        raise ValueError("no sections given")
    return out


def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _adsr(n: int, rate: int, a: float = 0.005, d: float = 0.1, s: float = 0.6, r: float = 0.1) -> np.ndarray:
    a_n, d_n, r_n = int(a * rate), int(d * rate), int(r * rate)
    e = np.full(n, s)
    head = e[:a_n]
    head[:] = np.linspace(0, 1, max(a_n, 1))[: len(head)]
    seg = e[a_n : a_n + d_n]
    seg[:] = np.linspace(1, s, max(d_n, 1))[: len(seg)]
    if r_n and n > r_n:
        e[-r_n:] *= np.linspace(1, 0, r_n)
    return e


def _filter(x: np.ndarray, rate: int, kind: str, cutoff: float | tuple[float, float], order: int) -> np.ndarray:
    n = len(x)
    size = 1 << int(np.ceil(np.log2(max(2 * n, 2))))
    f = np.fft.rfftfreq(size, 1.0 / rate)

    def low(fc: float) -> np.ndarray:
        return 1.0 / np.sqrt(1.0 + (f / fc) ** (2 * order))

    def high(fc: float) -> np.ndarray:
        return 1.0 / np.sqrt(1.0 + (fc / np.maximum(f, 1e-9)) ** (2 * order))

    if kind == "low":
        resp = low(min(cutoff, rate / 2 - 100))  # type: ignore[type-var,arg-type]
    elif kind == "high":
        resp = high(cutoff)  # type: ignore[arg-type]
    else:
        resp = high(cutoff[0]) * low(cutoff[1])  # type: ignore[index]
    return np.fft.irfft(np.fft.rfft(x, size) * resp, size)[:n]


def _saw(freq: float, n: int, rate: int, detune: float = 0.0) -> np.ndarray:
    ph = freq * (1 + detune) * np.arange(n) / rate
    return 2 * (ph - np.floor(ph + 0.5))


def _kick(n: int, rate: int) -> np.ndarray:
    t = np.arange(n) / rate
    f = 42 + 120 * np.exp(-t * 35)
    return np.sin(2 * np.pi * np.cumsum(f) / rate) * np.exp(-t * 7)


def _snare(n: int, rate: int, noise: _Noise) -> np.ndarray:
    t = np.arange(n) / rate
    body = _filter(noise.white(n), rate, "band", (900, 7000), 2)
    return body * np.exp(-t * 18) + 0.4 * np.sin(2 * np.pi * 190 * t) * np.exp(-t * 25)


def _hat(n: int, rate: int, noise: _Noise, open_: bool = False) -> np.ndarray:
    t = np.arange(n) / rate
    return _filter(noise.white(n), rate, "high", 7000, 4) * np.exp(-t * (12 if open_ else 60))


def _place(buf: np.ndarray, x: np.ndarray, at: float, rate: int) -> None:
    i = int(at * rate)
    if i >= len(buf):
        return
    m = min(len(x), len(buf) - i)
    buf[i : i + m] += x[:m]


def generate_bed(
    bpm: float,
    sections: Sequence[tuple[str, int]] | str,
    style: str = "pulse",
    key: str = "A",
    scale: str = "minor",
    seed: int = 3,
    fps: float = 60.0,
    end_hit: bool = False,
    gaps: Sequence[int] = (),
    rate: int = SAMPLE_RATE,
) -> Bed:
    """Synthesize a bed and its grid.

    `sections` is a list of `(name, bars)` or a string like `"intro:2,build:4,drop:4,outro:2"`.
    Names shape the arrangement: `intro` (pad and hat), `build` (adds kick, rising filter),
    `groove` (kit and bass, no arp: sits under a voice), `drop` (kit, bass and arp), `break`
    (pad only), `outro` (kit falls away), `hold` (sustained chord). `style` is `pulse`, `lofi`
    (swing, kick on beats 1 and 3 only) or `drive` (the build uses the full arrangement).
    `end_hit` adds a final kick and ring-out after the last bar. Each beat index in `gaps` is
    preceded by half a beat of silence (a pre-drop gap). The grid lists every beat with its
    time, bar, beat-in-bar, frame index at `fps` and section.
    """
    if bpm <= 0:
        raise ValueError("bpm must be positive")
    if fps <= 0:
        raise ValueError("fps must be positive")
    if style not in STYLES:
        raise ValueError(f"style must be one of {', '.join(STYLES)}")
    if scale not in SCALES:
        raise ValueError(f"scale must be one of {', '.join(SCALES)}")
    if key.upper() not in NOTES:
        raise ValueError(f"key must be one of {', '.join(NOTES)}")
    secs = parse_sections(sections) if isinstance(sections, str) else [(str(n), int(b)) for n, b in sections]
    if not secs or any(b < 1 for _, b in secs):
        raise ValueError("every section needs at least one bar")
    spb = 60.0 / bpm
    bars = sum(b for _, b in secs)
    total_beats = bars * 4
    for g in gaps:
        if not 1 <= g <= total_beats:
            raise ValueError(f"gap {g} is outside beats 1 to {total_beats}")
    noise = _Noise(seed)
    tail = 2.5 if end_hit else 0.5
    dur = bars * 4 * spb
    n = int((dur + tail) * rate)
    drums, bass, pad, arp = (np.zeros(n) for _ in range(4))
    root = 45 + NOTES[key.upper()]
    degs = SCALES[scale]

    def degree_note(deg: int, octave: int = 0) -> int:
        return root + 12 * octave + degs[deg % 7] + 12 * (deg // 7)

    swing = 0.08 if style == "lofi" else 0.0
    grid_beats: list[dict[str, Any]] = []
    bar_i = 0
    for name, nb in secs:
        for b in range(nb):
            bar_t = bar_i * 4 * spb
            chord_deg = PROGRESSIONS[scale][bar_i % 4]
            chord = [degree_note(chord_deg, 1), degree_note(chord_deg + 2, 1), degree_note(chord_deg + 4, 1)]
            progress = (b + 1) / nb
            full = name == "drop" or (name == "build" and style == "drive")
            groove = name == "groove"
            if name != "outro" or b < nb - 1:
                pn = int(4 * spb * rate)
                wave = sum(_saw(_hz(m), pn, rate, d) for m in chord for d in (-0.003, 0.003)) / 6
                fc = 900 + (3200 * progress if name == "build" else 1800 if full else 1100)
                _place(pad, _filter(wave, rate, "low", fc, 2) * _adsr(pn, rate, 0.25, 0.4, 0.8, 0.4) * (0.5 if full else 0.7), bar_t, rate)
            for k in range(4):
                t = bar_t + k * spb
                grid_beats.append(
                    {"i": len(grid_beats), "t": round(t, 4), "bar": bar_i + 1, "beat": k + 1,
                     "frame": frame_index(t, fps), "section": name}
                )
                if name in ("build", "drop", "groove", "outro") and not (name == "outro" and b == nb - 1 and k > 0):
                    if style != "lofi" or k in (0, 2):
                        _place(drums, _kick(int(0.45 * rate), rate) * 0.9, t, rate)
                if name in ("drop", "groove") or (name == "build" and progress > 0.5) or (name == "outro" and b < nb - 1):
                    if k in (1, 3):
                        _place(drums, _snare(int(0.3 * rate), rate, noise) * 0.35, t, rate)
                if name not in ("break", "hold"):
                    for h in (0, 0.5):
                        off = h * spb + (swing * spb if h else 0)
                        _place(drums, _hat(int(0.12 * rate), rate, noise, open_=bool(h and k == 3 and full)) * (0.12 if h else 0.08), t + off, rate)
                if full or groove or (name == "build" and progress > 0.5):
                    bn = int(spb * 0.9 * rate)
                    note = degree_note(chord_deg) if k != 3 else degree_note(chord_deg + 4)
                    _place(bass, _filter(_saw(_hz(note), bn, rate), rate, "low", 500, 2) * _adsr(bn, rate, 0.004, 0.12, 0.5, 0.05) * 0.5, t, rate)
                if full:
                    for s16 in range(2):
                        an = int(spb * 0.45 * rate)
                        note = chord[(k * 2 + s16) % 3] + 12
                        _place(arp, _filter(_saw(_hz(note), an, rate), rate, "low", 2600, 2) * _adsr(an, rate, 0.002, 0.08, 0.2, 0.05) * 0.18, t + s16 * spb / 2, rate)
            bar_i += 1
    if end_hit:
        _place(drums, _kick(int(1.2 * rate), rate), dur, rate)
        hn = int(2.3 * rate)
        last = [degree_note(0, 1), degree_note(2, 1), degree_note(4, 1), degree_note(0, 2)]
        chord_wave = sum(_saw(_hz(m), hn, rate, d) for m in last for d in (-0.003, 0.003)) / 8
        _place(pad, _filter(chord_wave, rate, "low", 1800, 2) * _adsr(hn, rate, 0.01, 0.3, 0.6, 1.5), dur, rate)
        grid_beats.append(
            {"i": len(grid_beats), "t": round(dur, 4), "bar": bars + 1, "beat": 1,
             "frame": frame_index(dur, fps), "section": "end-hit"}
        )
    for gb in gaps:
        g0, g1 = int((gb - 0.5) * spb * rate), int(gb * spb * rate)
        fade = int(0.004 * rate)
        for buf in (drums, bass, pad, arp):
            buf[g0:g1] = 0.0
            lo = max(g0 - fade, 0)
            buf[lo:g0] *= np.linspace(1, 0, g0 - lo)
    stems = {"drums": drums, "bass": bass, "pad": pad, "arp": arp}
    mix = drums + bass + pad + arp
    peak = float(np.max(np.abs(mix))) or 1.0
    gain = 10 ** (-6 / 20) / peak
    stems = {k: v * gain for k, v in stems.items()}
    sec_starts, t0 = [], 0.0
    for name, nb in secs:
        sec_starts.append({"name": name, "start": round(t0, 4), "bars": nb})
        t0 += nb * 4 * spb
    grid = {
        "bpm": bpm,
        "seconds_per_beat": round(spb, 6),
        "fps": fps,
        "frames_per_beat": round(spb * fps, 4),
        "beats_per_bar": 4,
        "duration": round(dur + (tail if end_hit else 0), 4),
        "music_end": round(dur, 4),
        "beats": grid_beats,
        "downbeats": [b["t"] for b in grid_beats if b["beat"] == 1],
        "sections": sec_starts,
        "style": style,
        "key": f"{key.upper()} {scale}",
        "seed": seed,
    }
    return Bed(stems, mix * gain, grid, rate)


def _stereo(x: np.ndarray, rate: int) -> np.ndarray:
    width = int(0.0004 * rate)
    return np.stack([x, np.concatenate([np.zeros(width), x[:-width]])], axis=1)


def write_bed(bed: Bed, out: str | Path) -> dict[str, str]:
    """Write `OUT.wav` (24-bit stereo), `OUT.stems/<stem>.wav` and `OUT.grid.json`.

    Loudness is left to the final mix: the bed peaks at -6 dBFS. Returns the paths written.
    """
    base = str(out)
    paths = {"mix": base + ".wav", "grid": base + ".grid.json", "stems": base + ".stems"}
    Path(paths["stems"]).mkdir(parents=True, exist_ok=True)
    for name, stem in bed.stems.items():
        write_wav24(Path(paths["stems"]) / f"{name}.wav", _stereo(stem, bed.rate), bed.rate)
    write_wav24(paths["mix"], _stereo(bed.mix, bed.rate), bed.rate)
    save_grid(bed.grid, paths["grid"])
    return paths
