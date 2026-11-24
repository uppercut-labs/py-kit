"""WAV duration, including the float files that the standard library's `wave` module refuses."""

from __future__ import annotations

import os
import struct


def wav_seconds(path: "str | os.PathLike[str]") -> float:
    """Length in seconds of a RIFF WAV of any sample format, read from its `fmt ` and `data` chunks.

    Raises ValueError for a file that has no `fmt ` or `data` chunk.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF WAV file")
    pos, rate, channels, bits, frames = 12, None, None, None, None
    while pos + 8 <= len(data):
        chunk_id, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if chunk_id == b"fmt ":
            _, channels, rate, _, _, bits = struct.unpack("<HHIIHH", data[pos + 8:pos + 24])
        elif chunk_id == b"data":
            if not (rate and channels and bits):
                raise ValueError("data chunk before the fmt chunk")
            frames = min(size, len(data) - pos - 8) // (channels * bits // 8)
            break
        pos += 8 + size + (size & 1)
    if frames is None or not rate:
        raise ValueError("no fmt and data chunks")
    return frames / rate
