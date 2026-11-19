import pytest
from PIL import Image

from uppercut_contact_sheet import (
    Tile,
    contact_sheet,
    crop_to_bbox,
    load_font,
    save_paged,
    tiles_from_paths,
)


def solid(w, h, color=(200, 30, 30)):
    return Image.new("RGB", (w, h), color)


def test_grid_size_without_captions():
    tiles = [Tile(solid(50, 40)) for _ in range(5)]
    sheet = contact_sheet(tiles, cols=2, cell=(60, 50), pad=4, caption="below")
    # no labels -> no caption strip
    assert sheet.size == (2 * 60 + 3 * 4, 3 * 50 + 4 * 4)


def test_caption_strip_adds_height_and_placement_changes_pixels():
    tiles = [Tile(solid(40, 40), "one"), Tile(solid(40, 40), "two\nlines")]
    below = contact_sheet(tiles, cols=2, cell=(80, 60), pad=4)
    above = contact_sheet(tiles, cols=2, cell=(80, 60), pad=4, caption="above")
    none = contact_sheet(tiles, cols=2, cell=(80, 60), pad=4, caption="none")
    assert below.size == above.size
    assert below.height > none.height
    assert below.tobytes() != above.tobytes()


def test_title_adds_height():
    tiles = [Tile(solid(10, 10))]
    assert contact_sheet(tiles, title="t").height > contact_sheet(tiles).height


def test_thumbnail_does_not_upscale_but_contain_and_nearest_do():
    tiles = [Tile(Image.new("RGB", (2, 2), "white"))]
    kw = dict(cell=(40, 40), pad=0, bg="black", caption="none", cols=1)
    thumb = contact_sheet(tiles, fit="thumbnail", **kw)
    contain = contact_sheet(tiles, fit="contain", **kw)
    nearest = contact_sheet(tiles, fit="nearest", **kw)
    assert thumb.getpixel((0, 0)) == (0, 0, 0)  # small image centred, corner stays background
    assert thumb.getpixel((20, 20)) == (255, 255, 255)
    assert contain.getpixel((1, 1)) == (255, 255, 255)
    assert nearest.getpixel((39, 39)) == (255, 255, 255)


def test_nearest_keeps_hard_edges():
    im = Image.new("RGB", (2, 1))
    im.putpixel((0, 0), (255, 0, 0))
    im.putpixel((1, 0), (0, 0, 255))
    sheet = contact_sheet([Tile(im)], fit="nearest", cell=(20, 10), pad=0, caption="none", cols=1)
    assert {sheet.getpixel((x, 5)) for x in range(20)} == {(255, 0, 0), (0, 0, 255)}


def test_transparent_image_shows_background():
    im = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    sheet = contact_sheet([Tile(im)], cell=(10, 10), pad=0, bg="#102030", caption="none", cols=1)
    assert sheet.getpixel((5, 5)) == (16, 32, 48)


def test_missing_and_error_tiles_draw_markers():
    plain = contact_sheet([Tile(solid(10, 10))], cell=(100, 40), pad=2, caption="none", cols=1)
    missing = contact_sheet([Tile(None)], cell=(100, 40), pad=2, caption="none", cols=1)
    error = contact_sheet([Tile(solid(10, 10), error="boom")], cell=(100, 40), pad=2, caption="none", cols=1)
    assert missing.tobytes() != Image.new("RGB", missing.size, "#111419").tobytes()
    assert error.tobytes() != plain.tobytes()


def test_border_draws_outline():
    sheet = contact_sheet([Tile(None)], cell=(20, 20), pad=3, border="#ff00ff", caption="none", cols=1)
    assert sheet.getpixel((3, 3)) == (255, 0, 255)


def test_long_label_is_clipped_to_cell():
    tiles = [Tile(solid(10, 10), "x" * 500)]
    sheet = contact_sheet(tiles, cell=(60, 40), pad=2, cols=1)
    assert sheet.width == 64


def test_validation_errors():
    t = [Tile(solid(5, 5))]
    with pytest.raises(ValueError):
        contact_sheet([])
    with pytest.raises(ValueError):
        contact_sheet(t, cols=0)
    with pytest.raises(ValueError):
        contact_sheet(t, cell=(0, 10))
    with pytest.raises(ValueError):
        contact_sheet(t, caption="side")
    with pytest.raises(ValueError):
        contact_sheet(t, fit="stretch")


def test_load_font_falls_back():
    f = load_font(14, ["/no/such/font.ttf"])
    assert f.getbbox("Ag")[3] > 0


def test_tiles_from_paths(tmp_path):
    good = tmp_path / "a.png"
    solid(8, 8).save(good)
    bad = tmp_path / "b.png"
    bad.write_bytes(b"not an image")
    tiles = tiles_from_paths([good, bad, tmp_path / "missing.png"])
    assert [t.label for t in tiles] == ["a.png", "b.png", "missing.png"]
    assert tiles[0].image is not None and tiles[0].error is None
    assert tiles[1].image is None and tiles[1].error
    assert tiles[2].image is None and tiles[2].error
    named = tiles_from_paths([good], labels=["custom"])
    assert named[0].label == "custom"
    with pytest.raises(ValueError):
        tiles_from_paths([good], labels=["a", "b"])


def test_crop_to_bbox_shares_one_box():
    a = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    a.paste((255, 0, 0, 255), (10, 10, 15, 15))
    b = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    b.paste((0, 255, 0, 255), (20, 12, 30, 20))
    out = crop_to_bbox([Tile(a, "a"), Tile(None, "gone"), Tile(b, "b")], margin=2)
    assert out[0].image.size == out[2].image.size == (24, 14)
    assert out[1].image is None
    assert out[0].label == "a"


def test_crop_to_bbox_margin_clamped_and_all_transparent_passthrough():
    a = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    a.putpixel((0, 0), (1, 2, 3, 255))
    assert crop_to_bbox([Tile(a)], margin=50)[0].image.size == (10, 10)
    empty = Tile(Image.new("RGBA", (5, 5), (0, 0, 0, 0)))
    assert crop_to_bbox([empty])[0].image.size == (5, 5)


def test_save_paged(tmp_path):
    tiles = [Tile(solid(10, 10), str(i)) for i in range(5)]
    paths = save_paged(tiles, tmp_path / "out" / "sheet", 2, cols=1, cell=(20, 20), title="Set")
    assert [p.name for p in paths] == ["sheet_01.jpg", "sheet_02.jpg", "sheet_03.jpg"]
    assert all(p.exists() for p in paths)
    assert Image.open(paths[2]).height < Image.open(paths[0]).height  # last page has one row
    png = save_paged(tiles[:1], tmp_path / "p", 5, ext="png", cols=1, cell=(20, 20))
    assert png[0].suffix == ".png"
    with pytest.raises(ValueError):
        save_paged(tiles, tmp_path / "x", 0)
    with pytest.raises(ValueError):
        save_paged([], tmp_path / "x", 3)
