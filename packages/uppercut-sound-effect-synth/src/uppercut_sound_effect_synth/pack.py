"""Render a set of sounds to WAV files with a manifest of SHA-256 hashes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from . import dsp
from .recipes import SOUNDS, Recipe, render
from .wav import write_wav


def render_pack(
    out: str | Path,
    names: Sequence[str] | None = None,
    seed: int = 7,
    take: int = 0,
    *,
    sounds: Mapping[str, Recipe] | None = None,
    rate: int = dsp.SAMPLE_RATE,
    bits: int = 24,
    channels: int = 2,
    peak_db: float = -3.0,
    preview: bool = False,
    gap: float = 0.5,
) -> dict[str, object]:
    """Write `<name>.wav` for each sound into `out` and a `manifest.json` beside them.

    `names` selects sounds (default: all). `sounds` replaces the built-in table with your
    own recipes. With `preview`, `_preview.wav` plays every sound in a row, `gap` seconds apart.
    Returns the manifest: settings plus, per sound, its file, duration and SHA-256.
    """
    table = SOUNDS if sounds is None else sounds
    chosen = list(table) if names is None else list(names)
    unknown = [n for n in chosen if n not in table]
    if unknown:
        raise KeyError(f"unknown sound {unknown[0]!r}; choose from {', '.join(sorted(table))}")
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    entries: dict[str, dict[str, object]] = {}
    parts: list[np.ndarray] = []
    for name in chosen:
        audio = render(name, seed, take, sounds=table, rate=rate, peak_db=peak_db, channels=channels)
        file = f"{name}.wav"
        digest = write_wav(folder / file, audio, rate, bits)
        entries[name] = {"file": file, "seconds": round(len(audio) / rate, 4), "sha256": digest}
        parts += [audio, np.zeros((int(gap * rate),) + audio.shape[1:])]
    if preview and parts:
        write_wav(folder / "_preview.wav", np.concatenate(parts), rate, bits)
    manifest: dict[str, object] = {
        "seed": seed,
        "take": take,
        "rate": rate,
        "bits": bits,
        "channels": channels,
        "peak_db": peak_db,
        "sounds": entries,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
