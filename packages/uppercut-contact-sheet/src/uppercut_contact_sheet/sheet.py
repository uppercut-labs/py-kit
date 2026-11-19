"""Labelled contact-sheet grids built with Pillow."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

from PIL import Image, ImageDraw, ImageFont

Color = str | tuple[int, ...]
Fit = Literal["thumbnail", "contain", "nearest"]
CaptionPlacement = Literal["below", "above", "none"]

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")

DEFAULT_FONTS: tuple[str, ...] = (
    "/System/Library/Fonts/Menlo.ttc",
    "C:/Windows/Fonts/consola.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "DejaVuSansMono.ttf",
)


@dataclass
class Tile:
    """One cell of a sheet.

    ``image`` of None draws a MISSING marker. ``error`` draws that message
    instead of the image. ``label`` may contain newlines for several lines.
    """

    image: Image.Image | None
    label: str = ""
    error: str | None = None


def load_font(size: int, candidates: Sequence[str] | None = None) -> ImageFont.ImageFont:
    """Return a font of ``size`` px, trying each candidate path or name in order.

    Falls back to Pillow's built-in font, which is scalable on Pillow 10.1+.
    """
    for name in (*(candidates or ()), *DEFAULT_FONTS):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow older than 10.1
        return ImageFont.load_default()


def tiles_from_paths(
    paths: Sequence[str | os.PathLike[str]],
    labels: Sequence[str] | None = None,
) -> list[Tile]:
    """Open each path as a tile. Unreadable files become error tiles.

    Labels default to the file name.
    """
    if labels is not None and len(labels) != len(paths):
        raise ValueError("labels must have the same length as paths")
    tiles: list[Tile] = []
    for i, p in enumerate(paths):
        label = labels[i] if labels is not None else Path(p).name
        try:
            with Image.open(p) as im:
                im.load()
                tiles.append(Tile(im.copy(), label))
        except Exception as exc:  # noqa: BLE001 - any decode failure becomes a marker tile
            tiles.append(Tile(None, label, error=f"{type(exc).__name__}"))
    return tiles


def crop_to_bbox(tiles: Sequence[Tile], margin: int = 6) -> list[Tile]:
    """Crop every tile to the union of the non-transparent areas of all tiles.

    All tiles are cut to the same box so they stay aligned. Tiles without an
    image, and tiles that are fully transparent, are passed through. The
    margin is added on each side and clamped to each image.
    """
    boxes = []
    for t in tiles:
        if t.image is None:
            continue
        box = t.image.convert("RGBA").getchannel("A").getbbox()
        if box:
            boxes.append(box)
    if not boxes:
        return list(tiles)
    x0 = min(b[0] for b in boxes) - margin
    y0 = min(b[1] for b in boxes) - margin
    x1 = max(b[2] for b in boxes) + margin
    y1 = max(b[3] for b in boxes) + margin
    out: list[Tile] = []
    for t in tiles:
        if t.image is None:
            out.append(t)
            continue
        w, h = t.image.size
        crop = t.image.crop((max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)))
        out.append(Tile(crop, t.label, t.error))
    return out


def _text_width(font: ImageFont.ImageFont, text: str) -> float:
    return font.getlength(text) if hasattr(font, "getlength") else float(font.getbbox(text)[2])


def _line_height(font: ImageFont.ImageFont) -> int:
    box = font.getbbox("Ag")
    return int(box[3] - box[1])


def _clip_line(font: ImageFont.ImageFont, text: str, width: int) -> str:
    if _text_width(font, text) <= width:
        return text
    ellipsis = "..."
    while text and _text_width(font, text + ellipsis) > width:
        text = text[:-1]
    return text + ellipsis if text else ""


def _fit_image(im: Image.Image, cell: tuple[int, int], fit: Fit) -> Image.Image:
    cw, ch = cell
    im = im.convert("RGBA")
    w, h = im.size
    if w <= 0 or h <= 0:
        raise ValueError("image has no pixels")
    scale = min(cw / w, ch / h)
    if fit == "thumbnail":
        scale = min(scale, 1.0)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    if (nw, nh) == (w, h):
        return im
    resample = Image.Resampling.NEAREST if fit == "nearest" else Image.Resampling.LANCZOS
    return im.resize((nw, nh), resample)


def contact_sheet(
    tiles: Sequence[Tile],
    *,
    cols: int = 4,
    cell: tuple[int, int] = (320, 200),
    fit: Fit = "thumbnail",
    bg: Color = "#111419",
    fg: Color = "white",
    caption: CaptionPlacement = "below",
    pad: int = 8,
    font: ImageFont.ImageFont | None = None,
    title: str | None = None,
    border: Color | None = None,
    error_color: Color = "#d24b50",
) -> Image.Image:
    """Lay ``tiles`` out in a grid and return an RGB image.

    ``cell`` is the picture area of each tile in pixels. ``fit`` picks the
    scaling: ``thumbnail`` shrinks only, ``contain`` scales up or down,
    ``nearest`` scales up or down without smoothing (pixel art). Pictures keep
    their aspect ratio and sit centred in the cell. Captions are drawn in a
    strip below or above the cell so they never cover the picture, and long
    lines are shortened with an ellipsis.
    """
    if cols < 1:
        raise ValueError("cols must be at least 1")
    if cell[0] < 1 or cell[1] < 1:
        raise ValueError("cell must be positive")
    if caption not in ("below", "above", "none"):
        raise ValueError(f"unknown caption placement: {caption!r}")
    if fit not in ("thumbnail", "contain", "nearest"):
        raise ValueError(f"unknown fit mode: {fit!r}")
    if not tiles:
        raise ValueError("no tiles to lay out")

    cw, ch = cell
    font = font or load_font(max(12, cw // 24))
    title_font = load_font(max(14, font.size + 4)) if hasattr(font, "size") else font
    line_h = _line_height(font)
    gap = 4
    lines = max((len(t.label.split("\n")) if t.label else 0) for t in tiles)
    cap_h = 0 if caption == "none" or lines == 0 else lines * (line_h + 2) + gap * 2
    title_h = _line_height(title_font) + 2 * pad if title else 0
    rows = (len(tiles) + cols - 1) // cols
    row_h = ch + cap_h
    width = cols * cw + (cols + 1) * pad
    height = title_h + rows * row_h + (rows + 1) * pad

    sheet = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(sheet)
    if title:
        draw.text((pad, pad), title, font=title_font, fill=fg)

    for i, tile in enumerate(tiles):
        x = pad + (i % cols) * (cw + pad)
        y = title_h + pad + (i // cols) * (row_h + pad)
        pic_y = y + (cap_h if caption == "above" else 0)
        if border is not None:
            draw.rectangle((x, pic_y, x + cw - 1, pic_y + ch - 1), outline=border)
        if tile.error is not None or tile.image is None:
            msg = tile.error if tile.error is not None else "MISSING"
            msg = _clip_line(font, msg, cw - 8)
            draw.text((x + 4, pic_y + ch // 2 - line_h // 2), msg, font=font, fill=error_color)
        else:
            pic = _fit_image(tile.image, cell, fit)
            px = x + (cw - pic.width) // 2
            py = pic_y + (ch - pic.height) // 2
            sheet.paste(pic, (px, py), pic)
        if cap_h and tile.label:
            cap_y = (y if caption == "above" else y + ch) + gap
            for n, line in enumerate(tile.label.split("\n")):
                draw.text((x + 2, cap_y + n * (line_h + 2)), _clip_line(font, line, cw - 4), font=font, fill=fg)
    return sheet


def save_paged(
    tiles: Sequence[Tile],
    out_prefix: str | os.PathLike[str],
    per_sheet: int,
    *,
    ext: str = "jpg",
    quality: int = 88,
    title: str | None = None,
    **options,
) -> list[Path]:
    """Split ``tiles`` into sheets of ``per_sheet`` and save ``<prefix>_01.<ext>`` and on.

    ``options`` go to :func:`contact_sheet`. When ``title`` is given, each
    sheet's title is followed by its page number. Returns the written paths.
    """
    if per_sheet < 1:
        raise ValueError("per_sheet must be at least 1")
    if not tiles:
        raise ValueError("no tiles to lay out")
    prefix = Path(out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    pages = (len(tiles) + per_sheet - 1) // per_sheet
    paths: list[Path] = []
    for n in range(pages):
        chunk = tiles[n * per_sheet:(n + 1) * per_sheet]
        page_title = f"{title}  ({n + 1}/{pages})" if title and pages > 1 else title
        img = contact_sheet(chunk, title=page_title, **options)
        path = prefix.with_name(f"{prefix.name}_{n + 1:02d}.{ext.lstrip('.')}")
        save_image(img, path, quality=quality)
        paths.append(path)
    return paths


def save_image(img: Image.Image, path: str | os.PathLike[str], *, quality: int = 88) -> Path:
    """Save as JPEG (with ``quality``) or any other format Pillow infers from the suffix."""
    p = Path(path)
    if p.suffix.lower() in (".jpg", ".jpeg"):
        img.convert("RGB").save(p, quality=quality)
    else:
        img.save(p)
    return p
