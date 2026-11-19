import struct
import zlib

import pytest

from uppercut_windows_window_capture import (
    Capture,
    WindowInfo,
    bgra_to_rgb,
    count_changed_pixels,
    encode_bmp,
    encode_png,
    frame_crop_box,
    match_text,
    select_windows,
    watch,
)
from uppercut_windows_window_capture.errors import WindowNotFoundError
from uppercut_windows_window_capture.image import format_for_path
from uppercut_windows_window_capture.matching import largest, wait_for


def gradient(w, h):
    out = bytearray()
    for y in range(h):
        for x in range(w):
            out += bytes((x * 10 % 256, y * 20 % 256, (x + y) * 5 % 256, 255))  # B, G, R, A
    return bytes(out)


def decode_png(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, ihdr = 8, b"", None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
        assert crc == zlib.crc32(tag + body) & 0xFFFFFFFF
        if tag == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    w, h, depth, color, _, _, _ = ihdr
    assert (depth, color) == (8, 2)
    raw = zlib.decompress(idat)
    rows = []
    for y in range(h):
        row = raw[y * (w * 3 + 1) : (y + 1) * (w * 3 + 1)]
        assert row[0] == 0
        rows.append(row[1:])
    return w, h, b"".join(rows)


# --- encoders ----------------------------------------------------------------------------------------------


def test_bgra_to_rgb_reorders_channels():
    assert bgra_to_rgb(bytes([1, 2, 3, 255, 4, 5, 6, 255]), 2, 1) == bytes([3, 2, 1, 6, 5, 4])


def test_png_round_trips_pixels():
    w, h = 7, 5
    bgra = gradient(w, h)
    dw, dh, rgb = decode_png(encode_png(bgra, w, h))
    assert (dw, dh) == (w, h)
    assert rgb == bgra_to_rgb(bgra, w, h)


def test_bmp_header_and_bottom_up_rows_with_padding():
    w, h = 3, 2  # 9 bytes per row, padded to 12
    bgra = gradient(w, h)
    data = encode_bmp(bgra, w, h)
    assert data[:2] == b"BM"
    size, _, _, offset = struct.unpack("<IHHI", data[2:14])
    assert size == len(data) and offset == 54
    width, height, planes, bpp = struct.unpack("<iiHH", data[18:30])
    assert (width, height, planes, bpp) == (3, 2, 1, 24)
    assert len(data) == 54 + 12 * 2
    first_row = data[54 : 54 + 9]  # the bottom image row comes first
    bottom = bgra[(h - 1) * w * 4 : h * w * 4]
    assert first_row == bytes(b for i in range(w) for b in bottom[i * 4 : i * 4 + 3])
    assert data[54 + 9 : 54 + 12] == b"\x00\x00\x00"


def test_encoders_reject_wrong_buffer_sizes():
    with pytest.raises(ValueError):
        encode_png(b"\x00" * 7, 2, 1)
    with pytest.raises(ValueError):
        encode_bmp(b"\x00" * 8, 0, 2)
    with pytest.raises(ValueError):
        Capture(2, 2, b"\x00" * 4)


def test_format_for_path():
    assert format_for_path("a/shot.PNG") == "png"
    assert format_for_path("a/shot.bmp") == "bmp"
    assert format_for_path("a/shot.dat", "bmp") == "bmp"
    with pytest.raises(ValueError):
        format_for_path("a/shot.jpg")
    with pytest.raises(ValueError):
        format_for_path("a/shot")


# --- Capture -----------------------------------------------------------------------------------------------


def test_save_writes_png_and_bmp_by_extension(tmp_path):
    cap = Capture(4, 3, gradient(4, 3))
    png = cap.save(tmp_path / "a.png")
    bmp = cap.save(tmp_path / "b.bmp")
    assert png.read_bytes()[:4] == b"\x89PNG"
    assert bmp.read_bytes()[:2] == b"BM"
    other = cap.save(tmp_path / "c.out", "png")
    assert other.read_bytes()[:4] == b"\x89PNG"


def test_crop_selects_the_rectangle_and_clamps():
    cap = Capture(4, 3, gradient(4, 3))
    sub = cap.crop(1, 1, 2, 2)
    assert (sub.width, sub.height) == (2, 2)
    assert sub.bgra[:4] == cap.bgra[(1 * 4 + 1) * 4 : (1 * 4 + 1) * 4 + 4]
    clamped = cap.crop(-5, -5, 100, 100)
    assert clamped.bgra == cap.bgra
    with pytest.raises(ValueError):
        cap.crop(10, 10, 2, 2)


def test_changed_pixels_counts_pixels_not_bytes():
    a = bytearray(gradient(10, 10))
    b = bytearray(a)
    b[0:3] = b"\x01\x02\x03"  # one pixel, three bytes
    b[4 * 50] ^= 0xFF  # another pixel
    assert count_changed_pixels(bytes(a), bytes(b)) == 2
    ca, cb = Capture(10, 10, bytes(a)), Capture(10, 10, bytes(b))
    assert ca.changed_pixels(cb) == 2
    assert ca.changed_pixels(ca) == 0
    assert ca.changed_pixels(Capture(5, 5, bytes(100))) == -1


def test_changed_pixels_across_chunk_boundaries():
    w = h = 100
    a = bytes(w * h * 4)
    b = bytearray(a)
    for i in (0, 1023, 1024, 5000, w * h - 1):
        b[i * 4] = 9
    assert count_changed_pixels(a, bytes(b)) == 5


def test_is_blank():
    assert Capture(3, 3, bytes([0, 0, 0, 255]) * 9).is_blank()
    flat = bytearray(bytes([0, 0, 0, 255]) * 9)
    flat[20] = 7
    assert not Capture(3, 3, bytes(flat)).is_blank()


def test_to_pil_matches_rgb_when_pillow_is_installed():
    pil = pytest.importorskip("PIL.Image")
    cap = Capture(3, 2, gradient(3, 2))
    img = cap.to_pil()
    assert img.size == (3, 2)
    assert img.tobytes() == cap.to_rgb()
    assert pil is not None


# --- matching ----------------------------------------------------------------------------------------------


def win(hwnd, title, cls="Cls", pid=1, size=(100, 100), visible=True):
    return WindowInfo(hwnd, title, cls, pid, (0, 0, size[0], size[1]), visible)


def test_match_text_modes():
    assert match_text("My Game - Level 2", "game")
    assert not match_text("My Game", "xyz")
    assert match_text("My Game", "my game", "exact")
    assert not match_text("My Game 2", "my game", "exact")
    assert match_text("Notes - draft.txt", "notes*.txt", "glob")
    assert not match_text("Other - draft.txt", "notes*.txt", "glob")
    assert match_text("Build 1234 done", r"build \d+", "regex")
    with pytest.raises(ValueError):
        match_text("a", "a", "fuzzy")


def test_select_windows_applies_every_filter():
    windows = [
        win(1, "Editor", "EditCls", pid=10),
        win(2, "Editor Preview", "PreviewCls", pid=10),
        win(3, "Editor hidden", "EditCls", pid=10, visible=False),
        win(4, "Other", "EditCls", pid=20),
        win(5, "Editor tiny", "EditCls", pid=10, size=(0, 50)),
    ]
    assert [w.hwnd for w in select_windows(windows, title="editor")] == [1, 2]
    assert [w.hwnd for w in select_windows(windows, title="editor", visible_only=False)] == [1, 2, 3]
    assert [w.hwnd for w in select_windows(windows, pid=20)] == [4]
    assert [w.hwnd for w in select_windows(windows, class_name="previewcls")] == [2]
    assert [w.hwnd for w in select_windows(windows, title="editor", match="exact")] == [1]
    assert select_windows(windows, title="nothing") == []
    with pytest.raises(ValueError):
        select_windows(windows, title="x", match="fuzzy")


def test_largest_picks_the_biggest_and_first_on_ties():
    windows = [win(1, "a", size=(10, 10)), win(2, "b", size=(200, 100)), win(3, "c", size=(200, 100))]
    assert largest(windows).hwnd == 2
    with pytest.raises(WindowNotFoundError):
        largest([])


def test_window_info_size():
    w = WindowInfo(1, "t", "c", 2, (10, 20, 110, 70))
    assert (w.width, w.height, w.area) == (100, 50, 5000)


def test_wait_for_retries_until_found_or_timeout():
    clock = {"t": 0.0}
    calls = []

    def probe():
        calls.append(clock["t"])
        return "found" if len(calls) == 3 else None

    def sleep(s):
        clock["t"] += s

    assert wait_for(probe, 10, interval=1.0, clock=lambda: clock["t"], sleep=sleep) == "found"
    assert calls == [0.0, 1.0, 2.0]

    clock["t"] = 0.0
    assert wait_for(lambda: None, 2.0, interval=1.0, clock=lambda: clock["t"], sleep=sleep) is None


def test_wait_for_tries_once_with_zero_timeout():
    seen = []
    assert wait_for(lambda: seen.append(1), 0.0, sleep=lambda s: seen.append("slept")) is None
    assert seen == [1]


def test_frame_crop_box():
    assert frame_crop_box((100, 100, 500, 400), (107, 100, 493, 393)) == (7, 0, 386, 293)
    with pytest.raises(ValueError):
        frame_crop_box((0, 0, 10, 10), (5, 5, 5, 9))


# --- series ------------------------------------------------------------------------------------------------


def make_grab(frames):
    it = iter(frames)
    return lambda: Capture(10, 10, next(it))


def test_watch_reports_live_when_pixels_change():
    base = bytes(400)
    moved = bytes([9]) * 400
    result = watch(make_grab([base, base, moved]), 3, 0.5, min_changed=50, sleep=lambda s: None)
    assert result.changed == [0, 100]
    assert result.live and result.moving_pairs == 1


def test_watch_reports_frozen_when_nothing_changes():
    base = bytes(400)
    slept = []
    result = watch(make_grab([base, base, base]), 3, 0.25, sleep=slept.append)
    assert not result.live
    assert slept == [0.25, 0.25]
    assert len(result.captures) == 3


def test_watch_ignores_changes_below_the_threshold():
    a = bytes(400)
    b = bytearray(a)
    b[0] = 1  # one pixel
    result = watch(make_grab([a, bytes(b)]), 2, 0, min_changed=2, sleep=lambda s: None)
    assert result.changed == [1] and not result.live


def test_watch_validates_arguments():
    with pytest.raises(ValueError):
        watch(lambda: None, 1)
    with pytest.raises(ValueError):
        watch(lambda: None, 2, -1)
