"""Window records and the rules for choosing among them. Pure Python, works on every platform."""

from __future__ import annotations

import fnmatch
import re
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence, TypeVar

from .errors import WindowNotFoundError

MATCH_MODES = ("substring", "exact", "glob", "regex")


@dataclass(frozen=True)
class WindowInfo:
    """A top-level window. `rect` is (left, top, right, bottom) in screen pixels."""

    hwnd: int
    title: str
    class_name: str
    pid: int
    rect: tuple[int, int, int, int]
    visible: bool = True

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]

    @property
    def area(self) -> int:
        return max(self.width, 0) * max(self.height, 0)


def match_text(text: str, pattern: str, mode: str = "substring") -> bool:
    """Case-insensitive match of `text` against `pattern`.

    Modes: "substring" (default), "exact", "glob" (`*` and `?`), "regex" (searched anywhere in the text).
    """
    if mode == "substring":
        return pattern.lower() in text.lower()
    if mode == "exact":
        return text.lower() == pattern.lower()
    if mode == "glob":
        return fnmatch.fnmatchcase(text.lower(), pattern.lower())
    if mode == "regex":
        return re.search(pattern, text, re.IGNORECASE) is not None
    raise ValueError("unknown match mode %r; use one of %s" % (mode, ", ".join(MATCH_MODES)))


def select_windows(
    windows: Iterable[WindowInfo],
    *,
    title: str | None = None,
    class_name: str | None = None,
    pid: int | None = None,
    match: str = "substring",
    visible_only: bool = True,
    min_size: int = 1,
) -> list[WindowInfo]:
    """Keep the windows that satisfy every given filter, in their original order.

    `title` and `class_name` use the `match` mode. Windows narrower or shorter than `min_size` pixels are dropped.
    """
    if match not in MATCH_MODES:
        raise ValueError("unknown match mode %r; use one of %s" % (match, ", ".join(MATCH_MODES)))
    kept = []
    for w in windows:
        if visible_only and not w.visible:
            continue
        if pid is not None and w.pid != pid:
            continue
        if title is not None and not match_text(w.title, title, match):
            continue
        if class_name is not None and not match_text(w.class_name, class_name, match):
            continue
        if w.width < min_size or w.height < min_size:
            continue
        kept.append(w)
    return kept


def largest(windows: Sequence[WindowInfo]) -> WindowInfo:
    """The window with the largest area; the first one wins a tie."""
    if not windows:
        raise WindowNotFoundError("no windows to choose from")
    return max(windows, key=lambda w: w.area)


T = TypeVar("T")


def wait_for(
    probe: Callable[[], T | None],
    timeout: float,
    *,
    interval: float = 0.25,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> T | None:
    """Call `probe` until it returns something other than None, or `timeout` seconds pass. Tries at least once."""
    end = clock() + timeout
    while True:
        value = probe()
        if value is not None:
            return value
        if clock() >= end:
            return None
        sleep(interval)


def frame_crop_box(
    window_rect: tuple[int, int, int, int], frame_rect: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    """The (left, top, width, height) of the visible frame inside a full-window capture.

    `window_rect` is the window rectangle and `frame_rect` the extended frame bounds, both in screen pixels. On
    Windows 10 and 11 the window rectangle includes an invisible resize border; the frame bounds do not.
    """
    left = max(frame_rect[0] - window_rect[0], 0)
    top = max(frame_rect[1] - window_rect[1], 0)
    width = frame_rect[2] - frame_rect[0]
    height = frame_rect[3] - frame_rect[1]
    if width <= 0 or height <= 0:
        raise ValueError("frame rectangle is empty")
    return left, top, width, height
