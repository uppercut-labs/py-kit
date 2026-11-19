"""Tempo, beat and downbeat detection.

The default backend is NumPy only: a spectral-flux onset envelope, then a grid search over
tempo and phase with an octave prior, refined by least squares. The `librosa` backend uses
librosa's beat tracker and needs `pip install uppercut-beat-grid[librosa]`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .audio import read_audio

ANALYSIS_RATE = 22050
MIN_CONFIDENCE = 3.0


def onset_envelope(y: np.ndarray, sr: int, hop_seconds: float = 0.003) -> tuple[np.ndarray, float]:
    """Spectral-flux onset strength. Returns `(envelope, seconds_per_frame)`.

    The signal is decimated to about 22 kHz, short-time spectra are log-compressed, and the
    positive change between consecutive frames is summed across frequency.
    """
    y = np.asarray(y, dtype=np.float64)
    factor = max(1, int(sr // ANALYSIS_RATE))
    if factor > 1:
        usable = len(y) // factor * factor
        y = y[:usable].reshape(-1, factor).mean(axis=1)
    rate = sr / factor
    n_fft = 512
    y = np.concatenate([np.zeros(n_fft // 2), y])  # centre each frame on its time stamp
    hop = max(1, int(round(hop_seconds * rate)))
    if len(y) < n_fft + hop:
        raise ValueError("audio is too short to analyse")
    window = np.hanning(n_fft)
    frames = 1 + (len(y) - n_fft) // hop
    env = np.zeros(frames)
    step = 1500
    for start in range(0, frames, step):
        stop = min(frames, start + step)
        first = max(start - 1, 0)  # one frame of overlap so the change across a chunk edge is counted
        idx = (np.arange(first, stop) * hop)[:, None] + np.arange(n_fft)[None, :]
        mag = np.log1p(80.0 * np.abs(np.fft.rfft(y[idx] * window, axis=1)) / n_fft)
        env[first + 1 : stop] = np.maximum(np.diff(mag, axis=0), 0).sum(axis=1)
    return env, hop / rate


def _sliding_max(x: np.ndarray, width: int) -> np.ndarray:
    pad = width // 2
    padded = np.pad(x, pad, mode="constant")
    return np.lib.stride_tricks.sliding_window_view(padded, width).max(axis=1)[: len(x)]


def _estimate_bpm(env: np.ndarray, hs: float, low: float = 60.0, high: float = 200.0) -> float:
    e = env - env.mean()
    spectrum = np.fft.rfft(e, 2 * len(e))
    ac = np.fft.irfft(spectrum * np.conj(spectrum))[: len(e)]
    lags = np.arange(len(ac)) * hs
    ok = (lags >= 60.0 / high) & (lags <= 60.0 / low)
    if not ok.any():
        return 120.0
    bpm = 60.0 / np.maximum(lags[ok], 1e-9)
    weight = np.exp(-0.5 * np.log2(bpm / 120.0) ** 2)
    return float(bpm[int(np.argmax(ac[ok] * weight))])


def fit_tempo(env: np.ndarray, hop_seconds: float, bpm_hint: float | None = None) -> dict[str, Any]:
    """Fit a constant tempo and phase to an onset envelope.

    Returns `{bpm, period, phase, confidence, stable, estimate_bpm, octaves, fit_residual_ms}`.
    `period` and `phase` are in seconds: beat k falls at `phase + k * period`. With `bpm_hint`
    only that tempo is searched; without it the half, same and double of the estimate compete
    and a prior around 120 BPM chooses between them. `confidence` is the fitted score relative
    to the envelope's average peak level; `stable` means it passed 3.0.
    """
    hs = hop_seconds
    env = np.asarray(env, dtype=np.float64)
    if len(env) < 8:
        raise ValueError("onset envelope is too short")
    length = len(env) * hs
    mx = _sliding_max(env, 2 * int(round(0.010 / hs)) + 1)
    base = float(mx.mean()) or 1e-9
    est = bpm_hint or _estimate_bpm(env, hs)

    def scores(period: float, phases: np.ndarray) -> np.ndarray:
        k = np.arange(int(length / period) + 1)
        bt = phases[:, None] + k[None, :] * period
        ok = bt < length - 0.011
        v = np.where(ok, mx[np.clip(np.round(bt / hs).astype(int), 0, len(mx) - 1)], 0)
        return v.sum(1) / np.maximum(ok.sum(1), 1)

    cands = []
    for mult in (1.0,) if bpm_hint else (0.5, 1.0, 2.0):
        t0 = 60.0 / (est * mult)
        best = (-1.0, t0, 0.0)
        for period in np.arange(t0 * 0.95, t0 * 1.05, t0 * 2e-4):
            phases = np.arange(0, period, 0.002)
            s = scores(period, phases)
            i = int(np.argmax(s))
            if s[i] > best[0]:
                best = (float(s[i]), float(period), float(phases[i]))
        bpm = 60.0 / best[1]
        prior = 1.0 if bpm_hint else float(np.exp(-0.5 * np.log2(bpm / 120.0) ** 2))
        cands.append(
            {
                "bpm": round(bpm, 3),
                "score": round(best[0], 4),
                "confidence": round(best[0] / base, 2),
                "prior": round(prior, 3),
                "period": best[1],
                "phase": best[2],
            }
        )
    pick = max(cands, key=lambda c: c["score"] * c["prior"])
    period, phase = pick["period"], pick["phase"]
    resid_ms = None
    for _ in range(2):  # least-squares refinement on the envelope peak nearest each beat
        k = np.arange(int((length - phase) / period))
        bt = phase + k * period
        lo = np.clip(np.round((bt - 0.025) / hs).astype(int), 0, len(env) - 1)
        w = int(round(0.05 / hs)) + 1
        idx = np.clip(lo[:, None] + np.arange(w)[None, :], 0, len(env) - 1)
        j = idx[np.arange(len(k)), np.argmax(env[idx], 1)]
        pt, pv = j * hs, env[j]
        keep = pv >= np.median(pv)
        if keep.sum() < 4:
            break
        sw = np.sqrt(pv[keep])
        a = np.stack([np.ones(keep.sum()), k[keep]], 1) * sw[:, None]
        (p0, p1), *_ = np.linalg.lstsq(a, pt[keep] * sw, rcond=None)
        if abs(p1 - period) > 0.005 * period:  # refinement only nudges
            break
        period, phase = float(p1), float(p0) % float(p1)
        resid_ms = float(np.median(np.abs(pt[keep] - (p0 + p1 * k[keep]))) * 1000)
    for c in cands:
        c["period"] = round(c["period"], 6)
        c["phase"] = round(c["phase"], 4)
    return {
        "bpm": round(60.0 / period, 3),
        "period": round(period, 6),
        "phase": round(phase, 4),
        "confidence": pick["confidence"],
        "stable": pick["confidence"] >= MIN_CONFIDENCE,
        "estimate_bpm": round(est, 2),
        "octaves": cands,
        "fit_residual_ms": None if resid_ms is None else round(resid_ms, 1),
    }


def pick_downbeat_phase(env: np.ndarray, hop_seconds: float, beats: np.ndarray, beats_per_bar: int = 4) -> int:
    """Which of the first `beats_per_bar` beats starts a bar: the phase whose beats carry the
    most onset strength. Returns 0 when there are too few beats to vote."""
    if len(beats) < beats_per_bar:
        return 0
    w = 3
    idx = np.clip(np.round(beats / hop_seconds).astype(int), 0, len(env) - 1)
    strength = np.array([env[max(i - w, 0) : i + w + 1].max() for i in idx])
    votes = [strength[p::beats_per_bar].mean() for p in range(beats_per_bar)]
    return int(np.argmax(votes))


def _onset_peaks(env: np.ndarray, hs: float) -> list[float]:
    if len(env) < 3:
        return []
    threshold = env.mean() + env.std()
    is_peak = (env[1:-1] > env[:-2]) & (env[1:-1] >= env[2:]) & (env[1:-1] > threshold)
    times = (np.flatnonzero(is_peak) + 1) * hs
    keep: list[float] = []
    for t in times:
        if not keep or t - keep[-1] >= 0.05:
            keep.append(float(t))
    return keep


def _beat_energy(y: np.ndarray, sr: int, beats: np.ndarray) -> list[float]:
    out = []
    for i, b in enumerate(beats):
        end = beats[i + 1] if i + 1 < len(beats) else b + 0.5
        seg = y[int(b * sr) : int(end * sr)]
        out.append(float(np.sqrt(np.mean(seg**2))) if len(seg) else 0.0)
    peak = max(out) if out else 0.0
    return [round(e / peak, 3) if peak else 0.0 for e in out]


def detect(
    audio: str | Path | np.ndarray,
    sr: int | None = None,
    backend: str = "fit",
    bpm_hint: float | None = None,
    beats_per_bar: int = 4,
) -> dict[str, Any]:
    """Detect tempo, beats and downbeats.

    `audio` is a file path or a mono sample array (then `sr` is required). `backend` is
    `"fit"` (NumPy) or `"librosa"`. Returns `{audio, duration, backend, tempo, beats,
    beats_per_bar, downbeat_phase, downbeats, energy, onsets}` plus, for the fit backend,
    `phase`, `period`, `confidence`, `stable` and `fit`. Times are seconds. The detected tempo
    can be off by a factor of two; pass `bpm_hint` to pin it.
    """
    if backend not in ("fit", "librosa"):
        raise ValueError("backend must be 'fit' or 'librosa'")
    if isinstance(audio, (str, Path)):
        label = Path(audio).name
        if backend == "librosa":
            y, rate = _load_librosa(audio)
        else:
            y, rate = read_audio(audio)
    else:
        if sr is None:
            raise ValueError("sr is required when audio is an array")
        label, y, rate = "", np.asarray(audio, dtype=np.float64), int(sr)
    duration = len(y) / rate
    env, hs = onset_envelope(y, rate)
    result: dict[str, Any] = {"audio": label, "duration": round(duration, 3), "backend": backend}
    if backend == "librosa":
        beats, tempo = _librosa_beats(y, rate, bpm_hint)
    else:
        fit = fit_tempo(env, hs, bpm_hint)
        period, phase = fit["period"], fit["phase"]
        beats = phase + np.arange(int((duration - phase) / period) + 1) * period
        beats = beats[beats <= duration]
        tempo = fit["bpm"]
        result.update(
            {"phase": fit["phase"], "period": fit["period"], "confidence": fit["confidence"],
             "stable": fit["stable"], "fit": fit}
        )
    phase_i = pick_downbeat_phase(env, hs, beats, beats_per_bar)
    result.update(
        {
            "tempo": round(float(tempo), 2),
            "beats": [round(float(b), 3) for b in beats],
            "beats_per_bar": beats_per_bar,
            "downbeat_phase": phase_i,
            "downbeats": [round(float(b), 3) for b in beats[phase_i::beats_per_bar]],
            "energy": _beat_energy(y, rate, beats),
            "onsets": [round(t, 3) for t in _onset_peaks(env, hs)],
        }
    )
    return result


def _import_librosa() -> Any:
    try:
        import librosa
    except ImportError as error:
        raise ImportError("the librosa backend needs: pip install uppercut-beat-grid[librosa]") from error
    return librosa


def _load_librosa(path: str | Path) -> tuple[np.ndarray, int]:
    librosa = _import_librosa()
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    return np.asarray(y, dtype=np.float64), int(sr)


def _librosa_beats(y: np.ndarray, sr: int, bpm_hint: float | None) -> tuple[np.ndarray, float]:
    librosa = _import_librosa()
    kwargs: dict[str, Any] = {"y": y.astype(np.float32), "sr": sr, "units": "frames"}
    if bpm_hint:
        kwargs["start_bpm"] = bpm_hint
    tempo, frames = librosa.beat.beat_track(**kwargs)
    return np.asarray(librosa.frames_to_time(frames, sr=sr)), float(np.atleast_1d(tempo)[0])


def grid_from_detection(result: dict[str, Any], fps: float = 60.0) -> dict[str, Any]:
    """Turn a `detect` result into a grid dict with frame indices, bars and beats-in-bar.

    Bar numbering starts at the first detected downbeat; earlier beats get bar 0.
    """
    from .grid import frame_index

    bpb = int(result.get("beats_per_bar", 4))
    first = int(result.get("downbeat_phase", 0))
    beats = []
    for i, t in enumerate(result["beats"]):
        pos = i - first
        beats.append(
            {
                "i": i,
                "t": t,
                "bar": pos // bpb + 1,
                "beat": pos % bpb + 1,
                "frame": frame_index(t, fps),
            }
        )
    return {
        "bpm": result["tempo"],
        "seconds_per_beat": round(60.0 / result["tempo"], 6),
        "fps": fps,
        "frames_per_beat": round(60.0 / result["tempo"] * fps, 4),
        "beats_per_bar": bpb,
        "duration": result["duration"],
        "beats": beats,
        "downbeats": result["downbeats"],
    }
