"""Exceptions raised by this package."""

from __future__ import annotations


class WindowCaptureError(Exception):
    """Base class for every error this package raises on purpose."""


class UnsupportedPlatformError(WindowCaptureError, RuntimeError):
    """Window capture was requested on a system other than Windows."""


class WindowNotFoundError(WindowCaptureError, LookupError):
    """No window matched the search."""


class CaptureFailedError(WindowCaptureError):
    """Windows would not hand over the window's pixels."""
