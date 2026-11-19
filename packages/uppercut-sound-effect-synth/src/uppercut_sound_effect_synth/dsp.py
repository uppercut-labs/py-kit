"""Signal building blocks. Signals are 1-D float64 NumPy arrays in about [-1, 1]."""

from __future__ import annotations

import numpy as np

from .rng import Rng

SAMPLE_RATE = 48000


def seconds(duration: float, rate: int = SAMPLE_RATE) -> int:
    """Number of samples in a duration, at least 1."""
    return max(1, int(round(duration * rate)))


def envelope(n: int, attack: float = 0.005, curve: float = 3.0, rate: int = SAMPLE_RATE) -> np.ndarray:
    """Linear attack over `attack` seconds, then a power-curve decay to zero over the rest."""
    if n < 1:
        raise ValueError("n must be at least 1")
    a = max(1, min(int(round(attack * rate)), n - 1)) if n > 1 else 1
    out = np.ones(n)
    out[:a] = np.linspace(0.0, 1.0, a)
    if n > a:
        out[a:] = np.power(np.linspace(1.0, 0.0, n - a), curve)
    return out


def exp_decay(n: int, time_constant: float, attack: float = 0.0, rate: int = SAMPLE_RATE) -> np.ndarray:
    """Optional linear attack, then exp(-t / time_constant)."""
    if time_constant <= 0:
        raise ValueError("time_constant must be positive")
    t = np.arange(n) / rate
    out = np.exp(-t / time_constant)
    if attack > 0:
        a = min(n, max(1, int(round(attack * rate))))
        out[:a] *= np.linspace(0.0, 1.0, a)
    return out


def fade(x: np.ndarray, fade_in: float = 0.0005, fade_out: float = 0.01, rate: int = SAMPLE_RATE) -> np.ndarray:
    """Linear fades at both ends so a sound never starts or stops with a click."""
    n = len(x)
    out = x.copy()
    i = min(n, int(round(fade_in * rate)))
    o = min(n, int(round(fade_out * rate)))
    if i > 0:
        out[:i] *= np.linspace(0.0, 1.0, i, endpoint=False)
    if o > 0:
        out[n - o :] *= np.linspace(1.0, 0.0, o)
    return out


def _response(freqs: np.ndarray, kind: str, cutoff: float | tuple[float, float], order: int) -> np.ndarray:
    def low(fc: float) -> np.ndarray:
        return 1.0 / np.sqrt(1.0 + (freqs / fc) ** (2 * order))

    def high(fc: float) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            r = 1.0 / np.sqrt(1.0 + (fc / np.maximum(freqs, 1e-9)) ** (2 * order))
        return r

    if kind == "low":
        return low(float(cutoff))  # type: ignore[arg-type]
    if kind == "high":
        return high(float(cutoff))  # type: ignore[arg-type]
    lo, hi = cutoff  # type: ignore[misc]
    return high(lo) * low(hi)


def _filter(x: np.ndarray, kind: str, cutoff: float | tuple[float, float], order: int, rate: int) -> np.ndarray:
    if order < 1:
        raise ValueError("order must be at least 1")
    n = len(x)
    size = 1 << int(np.ceil(np.log2(max(2 * n, 2))))
    spectrum = np.fft.rfft(x, size)
    freqs = np.fft.rfftfreq(size, 1.0 / rate)
    return np.fft.irfft(spectrum * _response(freqs, kind, cutoff, order), size)[:n]


def lowpass(x: np.ndarray, cutoff: float, order: int = 4, rate: int = SAMPLE_RATE) -> np.ndarray:
    """Zero-phase low-pass with a Butterworth magnitude response of the given order."""
    _check_cutoff(cutoff, rate)
    return _filter(x, "low", cutoff, order, rate)


def highpass(x: np.ndarray, cutoff: float, order: int = 4, rate: int = SAMPLE_RATE) -> np.ndarray:
    _check_cutoff(cutoff, rate)
    return _filter(x, "high", cutoff, order, rate)


def bandpass(x: np.ndarray, low: float, high: float, order: int = 4, rate: int = SAMPLE_RATE) -> np.ndarray:
    """Zero-phase band-pass between `low` and `high` Hz."""
    if low >= high:
        raise ValueError("low must be below high")
    _check_cutoff(low, rate)
    _check_cutoff(high, rate)
    return _filter(x, "band", (low, high), order, rate)


def _check_cutoff(cutoff: float, rate: int) -> None:
    if not 0 < cutoff < rate / 2:
        raise ValueError(f"cutoff {cutoff} must be between 0 and the Nyquist frequency {rate / 2}")


def band_noise(rng: Rng, n: int, low: float, high: float, rate: int = SAMPLE_RATE) -> np.ndarray:
    """White noise band-limited to `low`..`high` Hz (clamped to 20 Hz and just under Nyquist)."""
    lo = max(low, 20.0)
    hi = min(high, rate / 2 - 100.0)
    return bandpass(rng.white(n), lo, hi, rate=rate)


def sweep_lowpass(
    x: np.ndarray, f0: float, f1: float, order: int = 2, block: int = 512, rate: int = SAMPLE_RATE
) -> np.ndarray:
    """A low-pass whose cutoff moves from `f0` to `f1` Hz (geometrically) over the signal.

    Done by overlap-adding Hann-windowed blocks, each filtered at its own cutoff.
    """
    _check_cutoff(f0, rate)
    _check_cutoff(f1, rate)
    n = len(x)
    hop = block
    padded = np.concatenate([np.zeros(hop), x, np.zeros(2 * hop)])
    window = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(2 * hop) / (2 * hop))  # periodic Hann, sums to 1 at hop
    count = (n + hop) // hop + 1
    cutoffs = np.geomspace(f0, f1, count)
    size = 4 * hop
    out = np.zeros(len(padded) + size)
    freqs = np.fft.rfftfreq(size, 1.0 / rate)
    for k in range(count):
        start = k * hop
        seg = padded[start : start + 2 * hop] * window
        if len(seg) < 2 * hop:
            break
        filtered = np.fft.irfft(np.fft.rfft(seg, size) * _response(freqs, "low", cutoffs[k], order), size)
        out[start : start + size] += filtered
    return out[hop : hop + n]


def sweep(f0: float, f1: float, n: int, log: bool = True) -> np.ndarray:
    """A frequency curve of `n` samples from f0 to f1 Hz, geometric by default."""
    if log:
        if f0 <= 0 or f1 <= 0:
            raise ValueError("log sweeps need positive frequencies")
        return np.geomspace(f0, f1, n)
    return np.linspace(f0, f1, n)


def tone(freq: float | np.ndarray, n: int, rate: int = SAMPLE_RATE, phase: float = 0.0) -> np.ndarray:
    """A sine whose frequency is a constant or a per-sample curve. Phase is continuous."""
    f = np.full(n, float(freq)) if np.isscalar(freq) else np.asarray(freq, dtype=float)
    if len(f) != n:
        raise ValueError("frequency curve must have n samples")
    return np.sin(2.0 * np.pi * (np.cumsum(f) / rate + phase))


def saturate(x: np.ndarray, drive: float = 2.0) -> np.ndarray:
    """Soft clip: tanh of the signal after a gain of `drive`. Output stays within -1 to 1."""
    return np.tanh(x * drive)


def mix_into(buf: np.ndarray, x: np.ndarray, at: float, gain: float = 1.0, rate: int = SAMPLE_RATE) -> None:
    """Add `x` into `buf` starting `at` seconds, cut at the end of `buf`."""
    i = int(round(at * rate))
    if i < 0:
        raise ValueError("at must not be negative")
    if i >= len(buf):
        return
    m = min(len(x), len(buf) - i)
    buf[i : i + m] += x[:m] * gain


def db_to_gain(db: float) -> float:
    return 10.0 ** (db / 20.0)


def finish(
    x: np.ndarray,
    peak_db: float = -3.0,
    tail: float = 0.05,
    width: float = 0.15,
    channels: int = 2,
    rng: Rng | None = None,
    rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """The last step of a sound: a silent tail, a fixed peak level and optional stereo width.

    Returns shape (n,) for one channel or (n, 2) for two. Stereo width is a tiny delay on
    the right channel (random in 0.2 to 0.6 ms when `rng` is given, else 0.4 ms) blended
    with the dry signal by `width`. Silence stays silence.
    """
    if channels not in (1, 2):
        raise ValueError("channels must be 1 or 2")
    if peak_db > 0:
        raise ValueError("peak_db must be 0 or below")
    y = np.concatenate([x, np.zeros(int(round(tail * rate)))])
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > 0:
        y = y / peak * db_to_gain(peak_db)
    if channels == 1:
        return y
    delay = int(round((rng.uniform(0.0002, 0.0006) if rng else 0.0004) * rate))
    delay = max(1, min(delay, max(len(y) - 1, 1)))
    right = np.concatenate([np.zeros(delay), y[:-delay]]) * (1.0 - width) + y * width
    return np.stack([y, right], axis=1)
