"""Find and capture windows. Raises UnsupportedPlatformError when used outside Windows."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

from .errors import CaptureFailedError, UnsupportedPlatformError, WindowNotFoundError
from .image import Capture
from .matching import WindowInfo, frame_crop_box, largest, select_windows, wait_for

FALLBACKS = ("bitblt", "screen", "none")


def _backend() -> Any:
    if sys.platform != "win32":
        raise UnsupportedPlatformError(
            "uppercut-windows-window-capture works only on Windows (this system is %r)" % sys.platform
        )
    from . import _win32

    return _win32


def enable_dpi_awareness() -> None:
    """Make this process see real pixel sizes on scaled displays. Call it before finding or capturing windows."""
    _backend().enable_dpi_awareness()


def list_windows(*, visible_only: bool = True) -> list[WindowInfo]:
    """Every top-level window, in the system's enumeration order."""
    windows = _backend().enumerate_windows()
    return [w for w in windows if w.visible] if visible_only else windows


def find_windows(
    *,
    title: str | None = None,
    class_name: str | None = None,
    pid: int | None = None,
    match: str = "substring",
    visible_only: bool = True,
    min_size: int = 1,
) -> list[WindowInfo]:
    """Top-level windows that match every given filter. Needs at least one of title, class_name or pid."""
    if title is None and class_name is None and pid is None:
        raise ValueError("give at least one of title, class_name or pid")
    return select_windows(
        _backend().enumerate_windows(),
        title=title,
        class_name=class_name,
        pid=pid,
        match=match,
        visible_only=visible_only,
        min_size=min_size,
    )


def find_window(
    *,
    title: str | None = None,
    class_name: str | None = None,
    pid: int | None = None,
    match: str = "substring",
    prefer_largest: bool = False,
    timeout: float = 0.0,
    interval: float = 0.25,
) -> WindowInfo:
    """One matching window: the first in enumeration order, or the largest with `prefer_largest`.

    Keeps looking for up to `timeout` seconds. Raises WindowNotFoundError if nothing matches in time.
    """

    def probe() -> WindowInfo | None:
        found = find_windows(title=title, class_name=class_name, pid=pid, match=match)
        if not found:
            return None
        return largest(found) if prefer_largest else found[0]

    window = wait_for(probe, timeout, interval=interval)
    if window is None:
        wanted = ", ".join("%s=%r" % kv for kv in (("title", title), ("class_name", class_name), ("pid", pid)) if kv[1] is not None)
        raise WindowNotFoundError("no visible window matches %s" % wanted)
    return window


def capture_window(
    window: "int | WindowInfo",
    *,
    client_only: bool = False,
    fallback: str = "bitblt",
    full_content: bool = True,
    crop_to_frame: bool = False,
    restore: bool = False,
) -> Capture:
    """Capture one window with PrintWindow and return it as a `Capture`.

    client_only: capture only the client area, without title bar and borders.
    fallback: what to do if PrintWindow fails. "bitblt" copies from the window's own device context, "screen"
        copies the window's rectangle from the screen (anything covering the window shows up), "none" raises.
    full_content: pass PW_RENDERFULLCONTENT so DirectComposition and many GPU-drawn windows render.
    crop_to_frame: trim the invisible resize border that the full-window rectangle includes on Windows 10 and 11.
    restore: show a minimized window again, without activating it, before capturing.

    The window is never brought to the front or given focus. Raises CaptureFailedError if no pixels come back.
    """
    if fallback not in FALLBACKS:
        raise ValueError("unknown fallback %r; use one of %s" % (fallback, ", ".join(FALLBACKS)))
    api = _backend()
    hwnd = window.hwnd if isinstance(window, WindowInfo) else int(window)
    if not api.is_window(hwnd):
        raise WindowNotFoundError("window 0x%X no longer exists" % hwnd)
    if restore:
        api.restore_if_minimized(hwnd)
    width, height, bgra = api.grab(hwnd, client_only=client_only, fallback=fallback, full_content=full_content)
    title = window.title if isinstance(window, WindowInfo) else None
    shot = Capture(width, height, bgra, hwnd=hwnd, title=title)
    if crop_to_frame and not client_only:
        frame = api.frame_rect(hwnd)
        if frame is not None:
            left, top, w, h = frame_crop_box(api.window_rect(hwnd), frame)
            shot = shot.crop(left, top, w, h)
    return shot


@dataclass(frozen=True)
class SeriesResult:
    """Shots taken over time and how many pixels changed between each consecutive pair."""

    captures: list[Capture]
    changed: list[int]
    min_changed: int

    @property
    def moving_pairs(self) -> int:
        return sum(1 for c in self.changed if c >= self.min_changed)

    @property
    def live(self) -> bool:
        """True when at least one consecutive pair changed by `min_changed` pixels or more."""
        return self.moving_pairs > 0


def watch(
    grab: Callable[[], Capture],
    count: int,
    interval: float = 1.0,
    *,
    min_changed: int = 200,
    sleep: Callable[[float], None] = time.sleep,
) -> SeriesResult:
    """Call `grab` `count` times, `interval` seconds apart, and compare each shot with the one before."""
    if count < 2:
        raise ValueError("count must be at least 2 to compare shots")
    if interval < 0:
        raise ValueError("interval must not be negative")
    shots: list[Capture] = []
    for i in range(count):
        shots.append(grab())
        if i + 1 < count:
            sleep(interval)
    changed = [shots[i].changed_pixels(shots[i - 1]) for i in range(1, count)]
    return SeriesResult(shots, changed, min_changed)


def capture_series(window: "int | WindowInfo", count: int, interval: float = 1.0, *, min_changed: int = 200, **capture_options: Any) -> SeriesResult:
    """Capture a window `count` times and report whether its picture is changing (live) or stuck (frozen)."""
    return watch(lambda: capture_window(window, **capture_options), count, interval, min_changed=min_changed)
