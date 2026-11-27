"""Capture one window on Windows with PrintWindow, to PNG or BMP. Imports on every platform."""

from .capture import (
    SeriesResult,
    capture_series,
    capture_window,
    enable_dpi_awareness,
    find_window,
    find_windows,
    list_windows,
    watch,
)
from .errors import CaptureFailedError, UnsupportedPlatformError, WindowCaptureError, WindowNotFoundError
from .image import Capture, bgra_to_rgb, count_changed_pixels, encode_bmp, encode_png
from .matching import WindowInfo, frame_crop_box, match_text, select_windows

__all__ = [
    "Capture",
    "CaptureFailedError",
    "SeriesResult",
    "UnsupportedPlatformError",
    "WindowCaptureError",
    "WindowInfo",
    "WindowNotFoundError",
    "bgra_to_rgb",
    "capture_series",
    "capture_window",
    "count_changed_pixels",
    "enable_dpi_awareness",
    "encode_bmp",
    "encode_png",
    "find_window",
    "find_windows",
    "frame_crop_box",
    "list_windows",
    "match_text",
    "select_windows",
    "watch",
]
__version__ = "1.0.0"
