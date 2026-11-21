"""Pipe RGB frames from Pillow renders to ffmpeg as H.264, with an exit-code check, an upscale and a cached font loader."""

from ._encoder import EncodeError, RawVideoEncoder, ffmpeg_command, format_fps
from ._pixels import font, nearest

__all__ = [
    "EncodeError",
    "RawVideoEncoder",
    "ffmpeg_command",
    "font",
    "format_fps",
    "nearest",
]
__version__ = "0.1.1"
