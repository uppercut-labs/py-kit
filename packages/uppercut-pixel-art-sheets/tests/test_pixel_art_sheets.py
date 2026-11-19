import pytest
from PIL import Image, ImageDraw

from uppercut_pixel_art_sheets import (
    FONT_5X7,
    Origin,
    apply_palette,
    build_sheet,
    cut_corner_box,
    draw_text,
    force_corner_transparent,
    hex_to_rgb,
    indexed_to_rgba,
    nearest_scale,
    new_indexed,
    paste_cell,
    quantize_indexed,
    save_indexed,
    snap_to_bits,
    stamp_pattern,
    text_width,
)

PAL = [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]


def test_hex_to_rgb():
    assert hex_to_rgb("#ff8000") == (255, 128, 0)
    assert hex_to_rgb("00ff00") == (0, 255, 0)
    with pytest.raises(ValueError):
        hex_to_rgb("#fff")
    with pytest.raises(ValueError):
        hex_to_rgb("#gggggg")


def test_snap_to_bits():
    assert snap_to_bits((0, 255, 128)) == (0, 255, 132)
    assert snap_to_bits((10, 10, 10), bits=1) == (0, 0, 0)
    assert snap_to_bits((300, -5, 200), bits=8) == (255, 0, 200)
    with pytest.raises(ValueError):
        snap_to_bits((1, 2, 3), bits=0)


def test_new_indexed_and_apply_palette():
    im = new_indexed(4, 3, fill=2)
    assert im.mode == "P" and im.size == (4, 3) and im.getpixel((0, 0)) == 2
    apply_palette(im, PAL)
    assert im.getpalette()[:12] == [0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255]
    assert len(im.getpalette()) == 768
    assert im.info["transparency"] == 0
    apply_palette(im, PAL, transparency=None)
    assert "transparency" not in im.info
    apply_palette(im, PAL, transparency=bytes([0, 128, 255, 255]), size=16)
    assert im.info["transparency"] == bytes([0, 128, 255, 255])


def test_apply_palette_errors():
    with pytest.raises(ValueError):
        new_indexed(2, 2, fill=300)
    with pytest.raises(ValueError):
        apply_palette(Image.new("RGB", (2, 2)), PAL)
    with pytest.raises(ValueError):
        apply_palette(new_indexed(2, 2), PAL, size=2)
    with pytest.raises(ValueError):
        apply_palette(new_indexed(2, 2), PAL, size=0)


def test_save_indexed_roundtrip(tmp_path):
    im = new_indexed(4, 4)
    ImageDraw.Draw(im).rectangle((1, 1, 2, 2), fill=1)
    out = save_indexed(im, PAL, tmp_path / "deep" / "a.png", size=16)
    back = Image.open(out)
    assert back.mode == "P"
    assert back.getpixel((1, 1)) == 1 and back.getpixel((0, 0)) == 0
    assert back.info["transparency"] == 0
    assert back.convert("RGBA").getpixel((0, 0))[3] == 0
    assert back.convert("RGBA").getpixel((1, 1)) == (255, 0, 0, 255)


def test_force_corner_transparent_swaps_and_keeps_look():
    im = new_indexed(3, 1)
    apply_palette(im, PAL, transparency=None)
    im.putpixel((0, 0), 3)   # corner is blue
    im.putpixel((1, 0), 0)   # black
    im.putpixel((2, 0), 1)   # red
    before = im.convert("RGB").tobytes()
    out = force_corner_transparent(im)
    assert out.getpixel((0, 0)) == 0
    assert out.getpalette()[:3] == [0, 0, 255]
    assert out.info["transparency"] == 0
    assert out.convert("RGB").tobytes() == before


def test_force_corner_transparent_already_zero_and_rgb_rejected():
    im = apply_palette(new_indexed(2, 2), PAL, transparency=None)
    assert force_corner_transparent(im).info["transparency"] == 0
    with pytest.raises(ValueError):
        force_corner_transparent(Image.new("RGB", (2, 2)))


def test_quantize_indexed():
    src = Image.new("RGB", (8, 8), (10, 200, 10))
    ImageDraw.Draw(src).rectangle((2, 2, 5, 5), fill=(250, 10, 10))
    out = quantize_indexed(src, 4)
    assert out.mode == "P" and out.getpixel((0, 0)) == 0 and out.info["transparency"] == 0
    assert out.getpixel((3, 3)) != 0
    plain = quantize_indexed(src, 4, transparent_corner=False)
    assert "transparency" not in plain.info
    with pytest.raises(ValueError):
        quantize_indexed(src, 1)


def test_indexed_to_rgba():
    im = apply_palette(new_indexed(2, 1), PAL, transparency=None)
    im.putpixel((1, 0), 1)
    rgba = indexed_to_rgba(im)
    assert rgba.getpixel((0, 0))[3] == 0 and rgba.getpixel((1, 0)) == (255, 0, 0, 255)
    opaque = indexed_to_rgba(im, transparent_index=None)
    assert opaque.getpixel((0, 0))[3] == 255
    with pytest.raises(ValueError):
        indexed_to_rgba(Image.new("RGB", (1, 1)))


def test_stamp_pattern_and_scale():
    im = new_indexed(8, 8)
    d = ImageDraw.Draw(im)
    stamp_pattern(d, ("10", "01"), 1, 1, 3)
    assert im.getpixel((1, 1)) == 3 and im.getpixel((2, 2)) == 3 and im.getpixel((2, 1)) == 0
    big = new_indexed(8, 8)
    stamp_pattern(ImageDraw.Draw(big), ("1",), 2, 2, 1, scale=3)
    assert {big.getpixel((x, y)) for x in range(2, 5) for y in range(2, 5)} == {1}
    assert big.getpixel((5, 5)) == 0
    custom = new_indexed(4, 4)
    stamp_pattern(ImageDraw.Draw(custom), ("#.",), 0, 0, 2, ink="#")
    assert custom.getpixel((0, 0)) == 2 and custom.getpixel((1, 0)) == 0
    with pytest.raises(ValueError):
        stamp_pattern(ImageDraw.Draw(custom), ("1",), 0, 0, 1, scale=0)


def test_font_is_complete_and_rectangular():
    assert all(len(p) == 7 and all(len(r) == 5 for r in p) for p in FONT_5X7.values())
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .:?!-":
        assert ch in FONT_5X7
    assert len({FONT_5X7[c] for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"}) == 36


def test_draw_text_indexed_and_rgb():
    im = new_indexed(40, 10)
    adv = draw_text(ImageDraw.Draw(im), "ab", 1, 1, 2)
    assert adv == 12
    assert 2 in set(im.tobytes())
    assert im.getpixel((0, 0)) == 0
    rgb = Image.new("RGB", (60, 20))
    adv2 = draw_text(ImageDraw.Draw(rgb), "A", 0, 0, (255, 0, 0), scale=2, spacing=6)
    assert adv2 == 12
    assert rgb.getpixel((2, 0)) == (255, 0, 0) and rgb.getpixel((3, 1)) == (255, 0, 0)  # top bar of A, scaled
    assert rgb.getpixel((0, 0)) == (0, 0, 0)


def test_draw_text_unknown_char_draws_question_mark_and_case_folds():
    a, b = new_indexed(20, 10), new_indexed(20, 10)
    draw_text(ImageDraw.Draw(a), "☃", 0, 0, 1)
    draw_text(ImageDraw.Draw(b), "?", 0, 0, 1)
    assert a.tobytes() == b.tobytes()
    c, d = new_indexed(20, 10), new_indexed(20, 10)
    draw_text(ImageDraw.Draw(c), "q", 0, 0, 1)
    draw_text(ImageDraw.Draw(d), "Q", 0, 0, 1)
    assert c.tobytes() == d.tobytes()


def test_text_width():
    assert text_width("") == 0
    assert text_width("A") == 5
    assert text_width("ABC") == 17
    assert text_width("ABC", scale=2, spacing=7) == (14 + 5) * 2


def test_cut_corner_box():
    im = new_indexed(10, 10)
    cut_corner_box(ImageDraw.Draw(im), (1, 1, 8, 8), 2, outline=3)
    assert im.getpixel((1, 1)) == 0  # corner cut
    assert im.getpixel((2, 1)) == 3 and im.getpixel((4, 4)) == 2
    with pytest.raises(ValueError):
        cut_corner_box(ImageDraw.Draw(im), (1, 1, 2, 8), 2)


def test_origin_offsets_every_call():
    im = new_indexed(20, 20)
    o = Origin(ImageDraw.Draw(im), 10, 5)
    o.point((0, 0), 1)
    o.rect((1, 1, 2, 2), fill=2)
    o.line([(0, 5), (3, 5)], fill=3)
    o.ellipse((4, 8, 6, 10), fill=1)
    o.polygon([(8, 0), (9, 0), (9, 1)], fill=2)
    o.pattern(("1",), 5, 5, 3)
    assert o.text("A", 0, 10, 1) == 6
    assert im.getpixel((10, 5)) == 1
    assert im.getpixel((11, 6)) == 2
    assert im.getpixel((11, 10)) == 3
    assert im.getpixel((15, 10)) == 3
    assert im.getpixel((0, 0)) == 0


def test_nearest_scale():
    im = Image.new("RGB", (2, 1))
    im.putpixel((0, 0), (255, 0, 0))
    im.putpixel((1, 0), (0, 0, 255))
    big = nearest_scale(im, 3)
    assert big.size == (6, 3)
    assert big.getpixel((2, 2)) == (255, 0, 0) and big.getpixel((3, 0)) == (0, 0, 255)
    with pytest.raises(ValueError):
        nearest_scale(im, 0)


def test_paste_cell_and_errors():
    sheet = Image.new("RGB", (20, 10))
    paste_cell(sheet, Image.new("RGB", (4, 4), "white"), 1, 0, 10, 10)
    assert sheet.getpixel((10, 0)) == (255, 255, 255) and sheet.getpixel((14, 0)) == (0, 0, 0)
    with pytest.raises(ValueError):
        paste_cell(sheet, Image.new("RGB", (11, 4)), 0, 0, 10, 10)
    with pytest.raises(ValueError):
        paste_cell(sheet, Image.new("RGB", (4, 4)), 2, 0, 10, 10)
    with pytest.raises(ValueError):
        paste_cell(sheet, Image.new("RGB", (4, 4)), -1, 0, 10, 10)


def test_build_sheet_indexed_with_metadata(tmp_path):
    def cell(color):
        im = new_indexed(4, 4, color)
        return im

    tiles = [("a", cell(1)), ("b", cell(2)), cell(3)]
    sheet, meta = build_sheet(tiles, 2, 4, 4, PAL, size=16)
    assert sheet.mode == "P" and sheet.size == (8, 8)
    assert sheet.getpixel((0, 0)) == 1 and sheet.getpixel((4, 0)) == 2 and sheet.getpixel((0, 4)) == 3
    assert sheet.getpixel((7, 7)) == 0
    assert [m["name"] for m in meta] == ["a", "b", "tile_002"]
    assert meta[2] == {"index": 2, "name": "tile_002", "col": 0, "row": 1, "x": 0, "y": 4, "w": 4, "h": 4}
    out = save_indexed(sheet, PAL, tmp_path / "s.png", size=16)
    assert Image.open(out).getpixel((4, 0)) == 2


def test_build_sheet_rgba_and_errors():
    sheet, meta = build_sheet([Image.new("RGBA", (2, 2), (9, 9, 9, 255))], 3, 2, 2)
    assert sheet.mode == "RGBA" and sheet.size == (6, 2) and len(meta) == 1
    with pytest.raises(ValueError):
        build_sheet([], 2, 4, 4)
    with pytest.raises(ValueError):
        build_sheet([new_indexed(2, 2)], 0, 4, 4)
    with pytest.raises(ValueError):
        build_sheet([Image.new("RGB", (2, 2))], 1, 4, 4, PAL)
    with pytest.raises(ValueError):
        build_sheet([new_indexed(5, 2)], 1, 4, 4, PAL)
