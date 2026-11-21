from __future__ import annotations

import os
from functools import lru_cache

from PIL import Image, ImageFont


def nearest(img: Image.Image, n: int) -> Image.Image:
    """Scale up by a whole factor with no smoothing, for pixel art."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"n must be a whole number of at least 1, not {n!r}")
    return img.resize((img.width * n, img.height * n), Image.Resampling.NEAREST)


@lru_cache(maxsize=None)
def _load(path: str, size: int, index: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size, index=index)


def font(path: str | os.PathLike[str], size: int, *, index: int = 0) -> ImageFont.FreeTypeFont:
    """`ImageFont.truetype`, loaded once per (file, size, index) and shared after that. A render script asks for the
    same few sizes thousands of times, once per frame. Use `font.cache_clear()` to drop the cache."""
    return _load(os.fspath(path), int(size), int(index))


font.cache_clear = _load.cache_clear  # type: ignore[attr-defined]
