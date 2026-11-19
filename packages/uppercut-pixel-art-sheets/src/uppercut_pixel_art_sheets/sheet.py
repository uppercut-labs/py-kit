"""Packing cells into sheets and scaling."""

from __future__ import annotations

from typing import Any, Sequence

from PIL import Image

from .palette import RGB, apply_palette, Transparency


def nearest_scale(image: Image.Image, factor: int) -> Image.Image:
    """Scale up by a whole number with nearest-neighbour sampling."""
    if factor < 1:
        raise ValueError("factor must be at least 1")
    return image.resize((image.width * factor, image.height * factor), Image.Resampling.NEAREST)


def paste_cell(sheet: Image.Image, tile: Image.Image, col: int, row: int, cell_w: int, cell_h: int) -> None:
    """Paste ``tile`` into grid cell (``col``, ``row``) of a sheet made of ``cell_w`` by ``cell_h`` cells.

    The tile goes in the top-left of the cell. It must fit inside the cell and
    the cell must lie inside the sheet.
    """
    if col < 0 or row < 0:
        raise ValueError("col and row must not be negative")
    if tile.width > cell_w or tile.height > cell_h:
        raise ValueError(f"tile {tile.size} is larger than the {cell_w}x{cell_h} cell")
    x, y = col * cell_w, row * cell_h
    if x + cell_w > sheet.width or y + cell_h > sheet.height:
        raise ValueError("cell lies outside the sheet")
    sheet.paste(tile, (x, y))


def build_sheet(
    tiles: Sequence[Image.Image | tuple[str, Image.Image]],
    cols: int,
    cell_w: int,
    cell_h: int,
    palette: Sequence[RGB] | None = None,
    *,
    fill: int = 0,
    transparency: Transparency = 0,
    size: int = 256,
) -> tuple[Image.Image, list[dict[str, Any]]]:
    """Pack tiles left to right, top to bottom into one sheet.

    Each tile is an image or a ``(name, image)`` pair. With a ``palette`` the
    sheet is an indexed image with that palette applied (tiles must then be
    palette-mode images that share it). Without one the sheet is RGBA.
    Returns the sheet and one metadata dict per tile with ``index``, ``name``,
    ``col``, ``row``, ``x``, ``y``, ``w`` and ``h``.
    """
    if cols < 1:
        raise ValueError("cols must be at least 1")
    if not tiles:
        raise ValueError("no tiles to pack")
    rows = (len(tiles) + cols - 1) // cols
    if palette is not None:
        sheet = Image.new("P", (cols * cell_w, rows * cell_h), fill)
        apply_palette(sheet, palette, transparency=transparency, size=size)
    else:
        sheet = Image.new("RGBA", (cols * cell_w, rows * cell_h), (0, 0, 0, 0))
    meta: list[dict[str, Any]] = []
    for i, item in enumerate(tiles):
        name, tile = item if isinstance(item, tuple) else (f"tile_{i:03d}", item)
        col, row = i % cols, i // cols
        if palette is not None and tile.mode != "P":
            raise ValueError(f"tile {name!r} must be in P mode when a palette is given")
        paste_cell(sheet, tile, col, row, cell_w, cell_h)
        meta.append({"index": i, "name": name, "col": col, "row": row,
                     "x": col * cell_w, "y": row * cell_h, "w": tile.width, "h": tile.height})
    return sheet, meta
