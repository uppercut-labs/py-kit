"""Pixel buffers and image encoders. Pure Python, works on every platform."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

_CHUNK = 4096


def bgra_to_rgb(bgra: bytes, width: int, height: int) -> bytes:
    """Drop the alpha channel and reorder BGRA pixels to RGB."""
    _check_size(bgra, width, height)
    rgb = bytearray(width * height * 3)
    rgb[0::3] = bgra[2::4]
    rgb[1::3] = bgra[1::4]
    rgb[2::3] = bgra[0::4]
    return bytes(rgb)


def encode_bmp(bgra: bytes, width: int, height: int) -> bytes:
    """A 24-bit uncompressed BMP file from top-down BGRA pixels."""
    _check_size(bgra, width, height)
    row_bytes = width * 3
    padded = (row_bytes + 3) & ~3
    pad = b"\x00" * (padded - row_bytes)
    rows = []
    for y in range(height - 1, -1, -1):  # BMP rows run bottom to top
        row = bytearray(row_bytes)
        start = y * width * 4
        line = bgra[start : start + width * 4]
        row[0::3] = line[0::4]
        row[1::3] = line[1::4]
        row[2::3] = line[2::4]
        rows.append(bytes(row) + pad)
    pixels = b"".join(rows)
    header = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(pixels), 0, 0, 14 + 40)
    dib = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    return header + dib + pixels


def encode_png(bgra: bytes, width: int, height: int, *, compress_level: int = 6) -> bytes:
    """An 8-bit RGB PNG file from top-down BGRA pixels, written with the standard library."""
    _check_size(bgra, width, height)
    rgb = bgra_to_rgb(bgra, width, height)
    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type: none
        raw += rgb[y * stride : (y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), compress_level))
        + chunk(b"IEND", b"")
    )


def _check_size(bgra: bytes, width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    if len(bgra) != width * height * 4:
        raise ValueError("expected %d bytes for a %dx%d BGRA image, got %d" % (width * height * 4, width, height, len(bgra)))


def count_changed_pixels(a: bytes, b: bytes) -> int:
    """How many 4-byte pixels differ between two equally sized BGRA buffers. Returns -1 if the sizes differ."""
    if len(a) != len(b):
        return -1
    ma, mb = memoryview(a), memoryview(b)
    changed = 0
    for off in range(0, len(a), _CHUNK):
        ca, cb = ma[off : off + _CHUNK], mb[off : off + _CHUNK]
        if ca == cb:
            continue
        for p in range(0, len(ca), 4):
            if ca[p : p + 4] != cb[p : p + 4]:
                changed += 1
    return changed


def format_for_path(path: "str | Path", fmt: str | None = None) -> str:
    """"png" or "bmp", from `fmt` or else from the file extension."""
    if fmt is None:
        fmt = Path(path).suffix.lstrip(".")
    fmt = fmt.lower()
    if fmt not in ("png", "bmp"):
        raise ValueError("unsupported image format %r; use png or bmp" % (fmt or "(none)"))
    return fmt


@dataclass(frozen=True)
class Capture:
    """A captured window: top-down BGRA pixels, with alpha forced opaque by the capture functions."""

    width: int
    height: int
    bgra: bytes
    hwnd: int | None = None
    title: str | None = None

    def __post_init__(self) -> None:
        _check_size(self.bgra, self.width, self.height)

    def to_bytes(self, fmt: str = "png") -> bytes:
        fmt = format_for_path("", fmt)
        if fmt == "png":
            return encode_png(self.bgra, self.width, self.height)
        return encode_bmp(self.bgra, self.width, self.height)

    def save(self, path: "str | Path", fmt: str | None = None) -> Path:
        """Write a PNG or BMP file. The format comes from `fmt` or the extension. Returns the path."""
        kind = format_for_path(path, fmt)
        target = Path(path)
        target.write_bytes(self.to_bytes(kind))
        return target

    def to_rgb(self) -> bytes:
        return bgra_to_rgb(self.bgra, self.width, self.height)

    def to_pil(self) -> Any:
        """A Pillow RGB image. Needs the optional `pillow` extra."""
        try:
            from PIL import Image
        except ImportError as exc:
            raise ImportError("Capture.to_pil() needs Pillow: pip install 'uppercut-windows-window-capture[pillow]'") from exc
        return Image.frombytes("RGB", (self.width, self.height), self.to_rgb())

    def crop(self, left: int, top: int, width: int, height: int) -> "Capture":
        """A new Capture holding the given rectangle, clamped to the image. Raises ValueError if it is empty."""
        x0, y0 = max(0, left), max(0, top)
        x1, y1 = min(self.width, left + width), min(self.height, top + height)
        if x1 <= x0 or y1 <= y0:
            raise ValueError("crop rectangle is outside the image")
        stride = self.width * 4
        rows = [self.bgra[y * stride + x0 * 4 : y * stride + x1 * 4] for y in range(y0, y1)]
        return Capture(x1 - x0, y1 - y0, b"".join(rows), self.hwnd, self.title)

    def changed_pixels(self, other: "Capture") -> int:
        """Pixels that differ from `other`. Returns -1 if the sizes differ."""
        if (self.width, self.height) != (other.width, other.height):
            return -1
        return count_changed_pixels(self.bgra, other.bgra)

    def is_blank(self) -> bool:
        """True when every pixel has the same colour, such as the all-black result some GPU windows return."""
        first = self.bgra[:4]
        return self.bgra == first * (self.width * self.height)
