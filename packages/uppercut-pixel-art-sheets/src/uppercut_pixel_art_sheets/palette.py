"""Palettes and indexed-image helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

from PIL import Image

RGB = tuple[int, int, int]
Transparency = int | bytes | Sequence[int] | None


def hex_to_rgb(value: str) -> RGB:
    """Parse ``#rrggbb`` or ``rrggbb``."""
    text = value.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"expected a 6 digit hex color, got {value!r}")
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        raise ValueError(f"not a hex color: {value!r}") from None


def snap_to_bits(color: RGB, bits: int = 5) -> RGB:
    """Round each channel to ``bits`` bits and scale back to 8 bits.

    ``bits=5`` gives the 15-bit color of many handheld consoles.
    """
    if not 1 <= bits <= 8:
        raise ValueError("bits must be between 1 and 8")
    top = (1 << bits) - 1

    def one(v: int) -> int:
        level = round(max(0, min(255, v)) * top / 255)
        return round(level * 255 / top)

    return (one(color[0]), one(color[1]), one(color[2]))


def new_indexed(width: int, height: int, fill: int = 0) -> Image.Image:
    """A palette-mode image filled with palette index ``fill``."""
    if not 0 <= fill <= 255:
        raise ValueError("fill must be a palette index from 0 to 255")
    return Image.new("P", (width, height), fill)


def _flat_palette(palette: Sequence[RGB], size: int) -> list[int]:
    if len(palette) > size:
        raise ValueError(f"palette has {len(palette)} colors, more than size={size}")
    if not 1 <= size <= 256:
        raise ValueError("size must be between 1 and 256")
    flat = [c for color in palette for c in color]
    return flat + [0] * (size * 3 - len(flat))


def apply_palette(
    image: Image.Image,
    palette: Sequence[RGB],
    *,
    transparency: Transparency = 0,
    size: int = 256,
) -> Image.Image:
    """Set ``palette`` on a palette-mode image and return it.

    Unused entries up to ``size`` are filled with black. ``transparency`` is a
    palette index that saves as fully transparent (default 0), a bytes object
    with one alpha value per index, or None for an opaque image.
    """
    if image.mode != "P":
        raise ValueError(f"image must be in P mode, got {image.mode}")
    image.putpalette(_flat_palette(palette, size))
    if transparency is None:
        image.info.pop("transparency", None)
    else:
        image.info["transparency"] = transparency if isinstance(transparency, int) else bytes(transparency)
    return image


def save_indexed(
    image: Image.Image,
    palette: Sequence[RGB],
    path: str | os.PathLike[str],
    *,
    transparency: Transparency = 0,
    size: int = 256,
) -> Path:
    """Apply ``palette`` and save as an indexed PNG, creating parent folders."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    apply_palette(image, palette, transparency=transparency, size=size)
    image.save(out, optimize=False)
    return out


def force_corner_transparent(image: Image.Image) -> Image.Image:
    """Make the top-left pixel's color palette index 0 and mark index 0 transparent.

    The old index 0 and the corner index swap places in both the pixels and
    the palette, so the picture looks the same.
    """
    if image.mode != "P":
        raise ValueError(f"image must be in P mode, got {image.mode}")
    corner = image.getpixel((0, 0))
    if corner != 0:
        lut = list(range(256))
        lut[0], lut[corner] = corner, 0
        palette = image.getpalette() or []
        palette = palette + [0] * (768 - len(palette))
        swapped = palette[:]
        swapped[0:3], swapped[corner * 3:corner * 3 + 3] = palette[corner * 3:corner * 3 + 3], palette[0:3]
        image = image.point(lut)
        image.putpalette(swapped)
    image.info["transparency"] = 0
    return image


def quantize_indexed(image: Image.Image, colors: int = 16, *, transparent_corner: bool = True) -> Image.Image:
    """Reduce an image to an adaptive palette of ``colors`` entries.

    With ``transparent_corner`` the corner color becomes index 0 and
    transparent, which suits sprites drawn on a flat background.
    """
    if not 2 <= colors <= 256:
        raise ValueError("colors must be between 2 and 256")
    indexed = image.convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE, colors=colors)
    return force_corner_transparent(indexed) if transparent_corner else indexed


def indexed_to_rgba(image: Image.Image, *, transparent_index: int | None = 0) -> Image.Image:
    """Convert a palette-mode image to RGBA, making ``transparent_index`` fully transparent."""
    if image.mode != "P":
        raise ValueError(f"image must be in P mode, got {image.mode}")
    rgba = image.convert("RGB").convert("RGBA")
    if transparent_index is not None:
        mask = image.point(lambda v: 0 if v == transparent_index else 255, "L")
        rgba.putalpha(mask)
    return rgba
