"""A small seeded random source built on the PCG64 bit stream.

NumPy documents the raw PCG64 bit stream as stable, so this module turns raw 64-bit
integers into floats itself instead of relying on `Generator` methods whose output
may change between NumPy releases.
"""

from __future__ import annotations

import hashlib
from typing import Sequence, TypeVar

import numpy as np

T = TypeVar("T")


def derive_seed(seed: int, *labels: object) -> int:
    """A 64-bit seed derived from a base seed and labels such as a sound name and take number."""
    text = ":".join([str(int(seed)), *[str(label) for label in labels]])
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "little")


class Rng:
    """Seeded random numbers. The same seed gives the same sequence."""

    def __init__(self, seed: int) -> None:
        self.seed = int(seed)
        self._bits = np.random.PCG64(self.seed & ((1 << 64) - 1))

    def unit(self, size: int | None = None) -> float | np.ndarray:
        """Uniform in [0, 1)."""
        count = 1 if size is None else int(size)
        raw = self._bits.random_raw(count) if count else np.empty(0, dtype=np.uint64)
        values = (raw >> np.uint64(11)).astype(np.float64) * (1.0 / 9007199254740992.0)
        return float(values[0]) if size is None else values

    def uniform(self, low: float, high: float, size: int | None = None) -> float | np.ndarray:
        values = low + (high - low) * self.unit(size)
        return float(values) if size is None else values

    def normal(self, size: int) -> np.ndarray:
        """Standard normal samples (Box-Muller)."""
        count = int(size)
        pairs = (count + 1) // 2
        u1 = 1.0 - self.unit(pairs)  # in (0, 1]
        u2 = self.unit(pairs)
        radius = np.sqrt(-2.0 * np.log(u1))
        out = np.empty(pairs * 2)
        out[0::2] = radius * np.cos(2.0 * np.pi * u2)
        out[1::2] = radius * np.sin(2.0 * np.pi * u2)
        return out[:count]

    def white(self, size: int) -> np.ndarray:
        """White noise with unit variance."""
        return self.normal(size)

    def choice(self, options: Sequence[T]) -> T:
        if not options:
            raise ValueError("choice needs at least one option")
        return options[min(int(self.unit() * len(options)), len(options) - 1)]
