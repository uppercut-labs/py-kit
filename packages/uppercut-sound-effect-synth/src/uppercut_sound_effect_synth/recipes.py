"""Built-in sound recipes and the render functions that make them reproducible.

A recipe is a function `(rng, rate) -> 1-D array`. `render` seeds the generator from a base
seed, the sound name and a take number, so a given (seed, name, take) always gives the same
samples and a different take gives a different variation of the same sound.
"""

from __future__ import annotations

from typing import Callable, Mapping

import numpy as np

from . import dsp
from .rng import Rng, derive_seed

Recipe = Callable[[Rng, int], np.ndarray]


def whoosh(rng: Rng, rate: int = dsp.SAMPLE_RATE, duration: float = 0.7, up: bool = True) -> np.ndarray:
    n = dsp.seconds(duration, rate)
    x = dsp.sweep_lowpass(rng.white(n), 300 if up else 6000, 6000 if up else 300, rate=rate)
    return x * np.sin(np.pi * np.linspace(0, 1, n)) ** 2


def riser(rng: Rng, rate: int = dsp.SAMPLE_RATE, duration: float = 1.6) -> np.ndarray:
    n = dsp.seconds(duration, rate)
    ramp = np.linspace(0, 1, n) ** 2
    noise = dsp.sweep_lowpass(rng.white(n), 200, 9000, rate=rate) * ramp
    return noise + dsp.tone(dsp.sweep(180, 900, n), n, rate) * ramp * 0.5


def impact(rng: Rng, rate: int = dsp.SAMPLE_RATE, duration: float = 0.9) -> np.ndarray:
    n = dsp.seconds(duration, rate)
    f = dsp.sweep(58, 41, n)
    sub = dsp.tone(f, n, rate) * dsp.envelope(n, 0.004, 2.2, rate)
    harmonic = 0.35 * dsp.tone(f * 2, n, rate) * dsp.envelope(n, 0.004, 4.0, rate)
    click = dsp.band_noise(rng, n, 1500, 8000, rate) * dsp.envelope(n, 0.0004, 12.0, rate) * 0.6
    return sub + harmonic + click


def sub_drop(rng: Rng, rate: int = dsp.SAMPLE_RATE, duration: float = 1.2) -> np.ndarray:
    n = dsp.seconds(duration, rate)
    return dsp.tone(dsp.sweep(90, 30, n), n, rate) * dsp.envelope(n, 0.01, 1.6, rate)


def click(rng: Rng, rate: int = dsp.SAMPLE_RATE, duration: float = 0.08) -> np.ndarray:
    n = dsp.seconds(duration, rate)
    return dsp.band_noise(rng, n, 2000, 12000, rate) * dsp.envelope(n, 0.0002, 10.0, rate)


def tick(rng: Rng, rate: int = dsp.SAMPLE_RATE, duration: float = 0.05) -> np.ndarray:
    n = dsp.seconds(duration, rate)
    return dsp.tone(2400.0, n, rate) * dsp.envelope(n, 0.0001, 6.0, rate)


def pop(rng: Rng, rate: int = dsp.SAMPLE_RATE, duration: float = 0.12) -> np.ndarray:
    n = dsp.seconds(duration, rate)
    return dsp.tone(dsp.sweep(900, 280, n), n, rate) * dsp.envelope(n, 0.0004, 5.0, rate)


def shimmer(rng: Rng, rate: int = dsp.SAMPLE_RATE, duration: float = 1.0) -> np.ndarray:
    n = dsp.seconds(duration, rate)
    t = np.arange(n) / rate
    x = np.zeros(n)
    for k in range(7):
        freq = rng.uniform(2500, 7000)
        vibrato = 1 + 0.004 * np.sin(2 * np.pi * rng.uniform(4, 7) * t)
        phase = rng.uniform(0, 1)
        x += dsp.tone(freq * vibrato, n, rate, phase) / (k + 2)
    return x * dsp.envelope(n, 0.05, 2.5, rate)


def glitch(rng: Rng, rate: int = dsp.SAMPLE_RATE, duration: float = 0.35) -> np.ndarray:
    n = dsp.seconds(duration, rate)
    x = np.zeros(n)
    pos = 0
    while pos < n:
        length = min(int(rng.uniform(0.01, 0.05) * rate), n - pos)
        freq = rng.choice([220, 440, 880, 1760, 3520])
        square = np.sign(np.sin(2 * np.pi * freq * np.arange(length) / rate)) * rng.uniform(0.2, 1.0)
        x[pos : pos + length] = np.round(square * 4) / 4
        pos += length + int(rng.uniform(0, 0.02) * rate)
    return x * dsp.envelope(n, 0.0001, 1.0, rate)


def typing(rng: Rng, rate: int = dsp.SAMPLE_RATE, duration: float = 0.6, keys: int = 7) -> np.ndarray:
    n = dsp.seconds(duration, rate)
    x = np.zeros(n)
    m = int(0.03 * rate)
    for start in np.sort(rng.uniform(0, duration - 0.05, keys)):
        key = dsp.band_noise(rng, m, 1800, 9000, rate) * dsp.envelope(m, 0.0001, 8.0, rate) * rng.uniform(0.5, 1.0)
        i = int(start * rate)
        x[i : i + m] += key[: n - i]
    return x


def ding(rng: Rng, rate: int = dsp.SAMPLE_RATE, duration: float = 1.4) -> np.ndarray:
    n = dsp.seconds(duration, rate)
    partials = ((1320, 1.0), (2640, 0.35), (3960, 0.15), (1980, 0.2))
    bell = sum(dsp.tone(f, n, rate) * a for f, a in partials)
    return bell * dsp.envelope(n, 0.0006, 3.0, rate)


def swell(rng: Rng, rate: int = dsp.SAMPLE_RATE, duration: float = 1.8) -> np.ndarray:
    n = dsp.seconds(duration, rate)
    pad = sum(dsp.tone(f, n, rate, rng.uniform(0, 1)) for f in (220.0, 277.2, 329.6, 440.0))
    return pad * np.sin(np.pi * np.linspace(0, 1, n)) ** 1.5


SOUNDS: dict[str, Recipe] = {
    "whoosh": lambda r, sr: whoosh(r, sr, 0.7, True),
    "whoosh-short": lambda r, sr: whoosh(r, sr, 0.35, False),
    "riser": riser,
    "impact": impact,
    "sub-drop": sub_drop,
    "click": click,
    "tick": tick,
    "pop": pop,
    "shimmer": shimmer,
    "glitch": glitch,
    "type": typing,
    "ding": ding,
    "swell": swell,
}


def render(
    name: str,
    seed: int = 7,
    take: int = 0,
    *,
    sounds: Mapping[str, Recipe] | None = None,
    rate: int = dsp.SAMPLE_RATE,
    peak_db: float = -3.0,
    tail: float = 0.05,
    width: float = 0.15,
    channels: int = 2,
) -> np.ndarray:
    """Render one sound to a finished array: shape (n,) for mono or (n, 2) for stereo.

    The same (seed, name, take, options) always gives the same array. Raises KeyError for an
    unknown name.
    """
    table = SOUNDS if sounds is None else sounds
    if name not in table:
        raise KeyError(f"unknown sound {name!r}; choose from {', '.join(sorted(table))}")
    rng = Rng(derive_seed(seed, name, take))
    raw = table[name](rng, rate)
    return dsp.finish(raw, peak_db=peak_db, tail=tail, width=width, channels=channels, rng=rng, rate=rate)
