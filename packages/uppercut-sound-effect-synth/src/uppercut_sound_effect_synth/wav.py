"""WAV encoding with the standard library only. The same samples always give the same bytes."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import numpy as np

from .dsp import SAMPLE_RATE


def pcm_bytes(x: np.ndarray, bits: int = 24) -> bytes:
    """Interleaved little-endian PCM. 8-bit is unsigned, 16 and 24 are signed. Clips at full scale."""
    if bits not in (8, 16, 24):
        raise ValueError("bits must be 8, 16 or 24")
    a = np.clip(np.asarray(x, dtype=np.float64), -1.0, 1.0)
    if bits == 8:
        return np.round(a * 127.0 + 128.0).astype(np.uint8).tobytes()
    scale = float(2 ** (bits - 1) - 1)
    ints = np.round(a * scale).astype(np.int32)
    if bits == 16:
        return ints.astype("<i2").tobytes()
    raw = ints.astype("<i4").tobytes()
    return b"".join(raw[i : i + 3] for i in range(0, len(raw), 4))


def wav_bytes(x: np.ndarray, rate: int = SAMPLE_RATE, bits: int = 24) -> bytes:
    """A complete WAV file for samples shaped (n,) or (n, channels)."""
    a = np.asarray(x)
    if a.ndim == 1:
        channels = 1
    elif a.ndim == 2 and 1 <= a.shape[1] <= 8:
        channels = a.shape[1]
    else:
        raise ValueError("samples must be shaped (n,) or (n, channels) with 1 to 8 channels")
    data = pcm_bytes(a, bits)
    block = channels * bits // 8
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    fmt = b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * block, block, bits)
    return header + fmt + b"data" + struct.pack("<I", len(data)) + data + (b"\x00" if len(data) % 2 else b"")


def write_wav(path: str | Path, x: np.ndarray, rate: int = SAMPLE_RATE, bits: int = 24) -> str:
    """Write a WAV file and return its SHA-256 hex digest."""
    data = wav_bytes(x, rate, bits)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return hashlib.sha256(data).hexdigest()
