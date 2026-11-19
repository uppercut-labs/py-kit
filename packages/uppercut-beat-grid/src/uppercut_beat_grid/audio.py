"""Reading and writing audio. WAV works with the standard library; other formats need `soundfile`."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


def read_audio(path: str | Path) -> tuple[np.ndarray, int]:
    """Load a file as mono float64 in [-1, 1]; returns `(samples, sample_rate)`.

    16, 24 and 32-bit PCM WAV files load without extra packages. Everything else (FLAC, OGG,
    float WAV) needs `pip install uppercut-beat-grid[audio]`.
    """
    p = Path(path)
    if p.suffix.lower() in {".wav", ".wave"}:
        try:
            return _read_wav(p)
        except wave.Error:
            pass  # float or extensible WAV: let soundfile try
    try:
        import soundfile
    except ImportError as error:
        raise ImportError(
            f"reading {p.name} needs the soundfile package: pip install uppercut-beat-grid[audio]"
        ) from error
    data, rate = soundfile.read(str(p), dtype="float64", always_2d=True)
    return data.mean(axis=1), int(rate)


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(frames)
    if width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    elif width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        ints = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        ints = np.where(ints >= 1 << 23, ints - (1 << 24), ints)
        data = ints.astype(np.float64) / float(1 << 23)
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float64) / float(1 << 31)
    else:
        raise wave.Error(f"unsupported sample width {width}")
    return data.reshape(-1, channels).mean(axis=1), rate


def pcm24_bytes(x: np.ndarray) -> bytes:
    """Little-endian 24-bit PCM for samples shaped (n,) or (n, channels); clips at full scale."""
    ints = np.round(np.clip(np.asarray(x, dtype=np.float64), -1.0, 1.0) * float(2**23 - 1)).astype("<i4")
    raw = ints.tobytes()
    return b"".join(raw[i : i + 3] for i in range(0, len(raw), 4))


def write_wav24(path: str | Path, x: np.ndarray, rate: int) -> None:
    """Write a 24-bit PCM WAV for mono (n,) or stereo (n, 2) samples. Same samples, same bytes."""
    a = np.asarray(x)
    channels = 1 if a.ndim == 1 else a.shape[1]
    if a.ndim not in (1, 2) or not 1 <= channels <= 2:
        raise ValueError("samples must be shaped (n,) or (n, 2)")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(3)
        w.setframerate(rate)
        w.writeframes(pcm24_bytes(a))
