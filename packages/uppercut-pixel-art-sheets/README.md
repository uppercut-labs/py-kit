# uppercut-pixel-art-sheets

Pillow helpers for building indexed pixel-art sheets. It covers palettes with a reserved transparent index, stamping 1-bit patterns, drawing text with a built-in 5x7 bitmap font, drawing in a tile's own coordinates, packing tiles into a sheet grid with metadata, and saving indexed PNGs.

## Install

```sh
pip install uppercut-pixel-art-sheets
```

Requires Python 3.10 or newer and Pillow 10 or newer.

## Example

```python
from PIL import ImageDraw
from uppercut_pixel_art_sheets import (
    build_sheet, draw_text, hex_to_rgb, new_indexed, save_indexed, snap_to_bits,
)

palette = [snap_to_bits(hex_to_rgb(c)) for c in ("#000000", "#f4e5ba", "#b63f3b", "#4fb9c3")]

def tile(label, color):
    im = new_indexed(16, 16)            # index 0 is transparent
    d = ImageDraw.Draw(im)
    d.rectangle((1, 1, 14, 14), fill=color)
    draw_text(d, label, 5, 4, 1)
    return label, im

sheet, meta = build_sheet([tile("A", 2), tile("B", 3), tile("C", 2)], cols=2, cell_w=16, cell_h=16, palette=palette)
save_indexed(sheet, palette, "sheet.png")
print(meta[1])   # {'index': 1, 'name': 'B', 'col': 1, 'row': 0, 'x': 16, 'y': 0, 'w': 16, 'h': 16}
```

## Functions

| Name | What it does |
| --- | --- |
| `hex_to_rgb(value)` | Parses `#rrggbb`. |
| `snap_to_bits(color, bits=5)` | Rounds each channel to `bits` bits and back to 8, for console-style color depth. |
| `new_indexed(width, height, fill=0)` | A palette-mode image filled with one index. |
| `apply_palette(image, palette, *, transparency=0, size=256)` | Sets the palette, padded with black to `size` entries. `transparency` is a transparent index, a bytes object with one alpha per index, or `None`. |
| `save_indexed(image, palette, path, *, transparency=0, size=256)` | Applies the palette, creates parent folders and saves an indexed PNG. Returns the path. |
| `force_corner_transparent(image)` | Swaps the top-left pixel's color into index 0 and marks index 0 transparent. The picture looks the same. |
| `quantize_indexed(image, colors=16, *, transparent_corner=True)` | Adaptive palette reduction, optionally followed by `force_corner_transparent`. |
| `indexed_to_rgba(image, *, transparent_index=0)` | RGBA copy with one index made transparent. |
| `stamp_pattern(draw, pattern, x, y, color, *, scale=1, ink="1")` | Draws each `ink` character of a list of strings as a block. Works on indexed and RGB images. |
| `FONT_5X7` | Glyph table for letters, digits and common punctuation. |
| `draw_text(draw, text, x, y, color, *, font=FONT_5X7, spacing=6, scale=1)` | Draws upper-cased text, shows `?` for unknown characters, returns the advance in pixels. |
| `text_width(text, *, font=FONT_5X7, spacing=6, scale=1)` | Pixel width of a string. |
| `cut_corner_box(draw, box, fill, outline=None)` | Rectangle with its corner pixels cut off. |
| `Origin(draw, ox, oy)` | Offsets `point`, `line`, `rect`, `ellipse`, `polygon`, `pattern` and `text` by `(ox, oy)`. |
| `nearest_scale(image, factor)` | Whole-number nearest-neighbour upscale. |
| `paste_cell(sheet, tile, col, row, cell_w, cell_h)` | Pastes a tile into the top-left of a grid cell. |
| `build_sheet(tiles, cols, cell_w, cell_h, palette=None, *, fill=0, transparency=0, size=256)` | Packs tiles (images or `(name, image)` pairs) into a sheet and returns it with one metadata dict per tile. With a palette the sheet is indexed; without one it is RGBA. |

## Errors

All functions raise `ValueError` for bad input: an image that is not in palette mode where one is required, a palette larger than `size`, a tile bigger than its cell, an empty tile list, a scale or factor below 1, or a malformed hex color.

## License

MIT
