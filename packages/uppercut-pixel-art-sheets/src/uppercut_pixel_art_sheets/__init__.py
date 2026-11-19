"""Pillow helpers for indexed pixel-art sheets."""

from .draw import FONT_5X7, Origin, cut_corner_box, draw_text, stamp_pattern, text_width
from .palette import (
    apply_palette,
    force_corner_transparent,
    hex_to_rgb,
    indexed_to_rgba,
    new_indexed,
    quantize_indexed,
    save_indexed,
    snap_to_bits,
)
from .sheet import build_sheet, nearest_scale, paste_cell

__all__ = [
    "FONT_5X7",
    "Origin",
    "apply_palette",
    "build_sheet",
    "cut_corner_box",
    "draw_text",
    "force_corner_transparent",
    "hex_to_rgb",
    "indexed_to_rgba",
    "nearest_scale",
    "new_indexed",
    "paste_cell",
    "quantize_indexed",
    "save_indexed",
    "snap_to_bits",
    "stamp_pattern",
    "text_width",
]

__version__ = "0.1.0"
