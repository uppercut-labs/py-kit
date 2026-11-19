"""Win32 calls through ctypes. Import this module only on Windows."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

from .errors import CaptureFailedError, UnsupportedPlatformError
from .matching import WindowInfo

if sys.platform != "win32":  # pragma: no cover - guarded by the caller as well
    raise UnsupportedPlatformError("window capture works only on Windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
DWMWA_EXTENDED_FRAME_BOUNDS = 9
SW_SHOWNOACTIVATE = 4

HANDLE = ctypes.c_void_p
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, HANDLE, wt.LPARAM)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


def _sig(func, restype, *argtypes):
    func.restype = restype
    func.argtypes = list(argtypes)


# Explicit signatures keep 64-bit handles from being truncated to 32-bit ints.
_sig(user32.EnumWindows, wt.BOOL, WNDENUMPROC, wt.LPARAM)
_sig(user32.IsWindowVisible, wt.BOOL, HANDLE)
_sig(user32.IsWindow, wt.BOOL, HANDLE)
_sig(user32.IsIconic, wt.BOOL, HANDLE)
_sig(user32.ShowWindow, wt.BOOL, HANDLE, ctypes.c_int)
_sig(user32.GetWindowTextLengthW, ctypes.c_int, HANDLE)
_sig(user32.GetWindowTextW, ctypes.c_int, HANDLE, wt.LPWSTR, ctypes.c_int)
_sig(user32.GetClassNameW, ctypes.c_int, HANDLE, wt.LPWSTR, ctypes.c_int)
_sig(user32.GetWindowThreadProcessId, wt.DWORD, HANDLE, ctypes.POINTER(wt.DWORD))
_sig(user32.GetWindowRect, wt.BOOL, HANDLE, ctypes.POINTER(wt.RECT))
_sig(user32.GetClientRect, wt.BOOL, HANDLE, ctypes.POINTER(wt.RECT))
_sig(user32.ClientToScreen, wt.BOOL, HANDLE, ctypes.POINTER(wt.POINT))
_sig(user32.GetDC, HANDLE, HANDLE)
_sig(user32.ReleaseDC, ctypes.c_int, HANDLE, HANDLE)
_sig(user32.PrintWindow, wt.BOOL, HANDLE, HANDLE, wt.UINT)
_sig(gdi32.CreateCompatibleDC, HANDLE, HANDLE)
_sig(gdi32.CreateCompatibleBitmap, HANDLE, HANDLE, ctypes.c_int, ctypes.c_int)
_sig(gdi32.SelectObject, HANDLE, HANDLE, HANDLE)
_sig(gdi32.DeleteObject, wt.BOOL, HANDLE)
_sig(gdi32.DeleteDC, wt.BOOL, HANDLE)
_sig(gdi32.BitBlt, wt.BOOL, HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, HANDLE,
     ctypes.c_int, ctypes.c_int, wt.DWORD)
_sig(gdi32.GetDIBits, ctypes.c_int, HANDLE, HANDLE, wt.UINT, wt.UINT, ctypes.c_void_p,
     ctypes.POINTER(BITMAPINFO), wt.UINT)
_sig(dwmapi.DwmGetWindowAttribute, ctypes.c_long, HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD)


def enable_dpi_awareness() -> None:
    """Make this process report real pixel sizes on scaled displays. Safe to call more than once."""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def _text(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rc = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rc)):
        raise CaptureFailedError("GetWindowRect failed for window 0x%X" % hwnd)
    return rc.left, rc.top, rc.right, rc.bottom


def frame_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """The visible frame bounds from the desktop window manager, or None if unavailable."""
    rc = wt.RECT()
    hr = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rc), ctypes.sizeof(rc))
    if hr != 0:
        return None
    return rc.left, rc.top, rc.right, rc.bottom


def enumerate_windows() -> list[WindowInfo]:
    found: list[WindowInfo] = []

    def callback(hwnd, _lparam):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rc = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rc))
        found.append(
            WindowInfo(
                hwnd=int(hwnd or 0),
                title=_text(hwnd),
                class_name=_class(hwnd),
                pid=int(pid.value),
                rect=(rc.left, rc.top, rc.right, rc.bottom),
                visible=bool(user32.IsWindowVisible(hwnd)),
            )
        )
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found


def is_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


def restore_if_minimized(hwnd: int) -> bool:
    """Show a minimized window again without activating it. Returns True if it was minimized."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        return True
    return False


def grab(hwnd: int, *, client_only: bool, fallback: str, full_content: bool) -> tuple[int, int, bytes]:
    """Return (width, height, top-down BGRA bytes with opaque alpha)."""
    rc = wt.RECT()
    getter = user32.GetClientRect if client_only else user32.GetWindowRect
    if not getter(hwnd, ctypes.byref(rc)):
        raise CaptureFailedError("could not read the size of window 0x%X" % hwnd)
    width, height = rc.right - rc.left, rc.bottom - rc.top
    if width <= 0 or height <= 0:
        raise CaptureFailedError("window 0x%X has no visible area (%dx%d)" % (hwnd, width, height))

    hdc_window = user32.GetDC(hwnd)
    if not hdc_window:
        raise CaptureFailedError("GetDC failed for window 0x%X" % hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    old = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        flags = (PW_CLIENTONLY if client_only else 0) | (PW_RENDERFULLCONTENT if full_content else 0)
        ok = bool(user32.PrintWindow(hwnd, hdc_mem, flags))
        if not ok and fallback == "bitblt":
            ok = bool(gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_window, 0, 0, SRCCOPY))
        elif not ok and fallback == "screen":
            if client_only:
                origin = wt.POINT(0, 0)
                user32.ClientToScreen(hwnd, ctypes.byref(origin))
                sx, sy = origin.x, origin.y
            else:
                sx, sy = rc.left, rc.top
            hdc_screen = user32.GetDC(None)
            try:
                ok = bool(gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, sx, sy, SRCCOPY | CAPTUREBLT))
            finally:
                user32.ReleaseDC(None, hdc_screen)
        if not ok:
            raise CaptureFailedError("PrintWindow failed for window 0x%X (fallback: %s)" % (hwnd, fallback))

        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # top-down
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        buf = ctypes.create_string_buffer(width * height * 4)
        # The bitmap must not be selected into a DC while GetDIBits reads it.
        gdi32.SelectObject(hdc_mem, old)
        old = None
        got = gdi32.GetDIBits(hdc_mem, hbmp, 0, height, buf, ctypes.byref(info), DIB_RGB_COLORS)
        if got != height:
            raise CaptureFailedError("GetDIBits returned %d of %d rows" % (got, height))
        data = bytearray(buf.raw)
        data[3::4] = b"\xff" * (width * height)
        return width, height, bytes(data)
    finally:
        if old:
            gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_window)
