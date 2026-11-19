"""Deterministic procedural sound effects."""

from .dsp import (
    SAMPLE_RATE,
    band_noise,
    bandpass,
    db_to_gain,
    envelope,
    exp_decay,
    fade,
    finish,
    highpass,
    lowpass,
    mix_into,
    saturate,
    seconds,
    sweep,
    sweep_lowpass,
    tone,
)
from .pack import render_pack
from .recipes import SOUNDS, Recipe, render
from .rng import Rng, derive_seed
from .wav import pcm_bytes, wav_bytes, write_wav

__all__ = [
    "SAMPLE_RATE",
    "SOUNDS",
    "Recipe",
    "Rng",
    "band_noise",
    "bandpass",
    "db_to_gain",
    "derive_seed",
    "envelope",
    "exp_decay",
    "fade",
    "finish",
    "highpass",
    "lowpass",
    "mix_into",
    "pcm_bytes",
    "render",
    "render_pack",
    "saturate",
    "seconds",
    "sweep",
    "sweep_lowpass",
    "tone",
    "wav_bytes",
    "write_wav",
]
__version__ = "0.1.0"
