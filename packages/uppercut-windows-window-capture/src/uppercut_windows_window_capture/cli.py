"""Command line interface: `uppercut-window-capture` or `python -m uppercut_windows_window_capture`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .errors import CaptureFailedError, UnsupportedPlatformError, WindowCaptureError, WindowNotFoundError

EXIT_OK = 0
EXIT_NOT_FOUND = 2
EXIT_FROZEN = 3
EXIT_CAPTURE_FAILED = 4
EXIT_UNSUPPORTED = 5
EXIT_USAGE = 64


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse would exit with 2, which this tool uses for "not found"
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, "%s: error: %s\n" % (self.prog, message))


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="uppercut-window-capture",
        description="Capture one window on Windows with PrintWindow, to PNG or BMP.",
        epilog="Exit codes: 0 ok, 2 window not found, 3 series is frozen, 4 capture failed, 5 not Windows, 64 usage.",
    )
    find = p.add_argument_group("find the window")
    find.add_argument("--title", help="window title to match (case-insensitive)")
    find.add_argument("--class", dest="class_name", help="window class name to match")
    find.add_argument("--pid", type=int, help="process id that owns the window")
    find.add_argument("--match", choices=("substring", "exact", "glob", "regex"), default="substring",
                      help="how --title and --class are matched (default: substring)")
    find.add_argument("--largest", action="store_true", help="when several windows match, use the largest")
    find.add_argument("--wait", type=float, default=0.0, metavar="SECONDS", help="keep looking this long for the window")
    find.add_argument("--list", action="store_true", help="print the matching windows and exit")

    cap = p.add_argument_group("capture")
    cap.add_argument("--client", action="store_true", help="capture only the client area")
    cap.add_argument("--fallback", choices=("bitblt", "screen", "none"), default="bitblt",
                     help="what to do if PrintWindow fails (default: bitblt)")
    cap.add_argument("--no-full-content", action="store_true", help="leave out PW_RENDERFULLCONTENT")
    cap.add_argument("--crop-frame", action="store_true", help="trim the invisible resize border")
    cap.add_argument("--crop", metavar="X,Y,W,H", help="keep only this rectangle of the capture")
    cap.add_argument("--restore", action="store_true", help="show a minimized window again, without activating it")
    cap.add_argument("--dpi-aware", action="store_true", help="report real pixel sizes on scaled displays")
    cap.add_argument("--fail-on-blank", action="store_true", help="exit 4 if the capture is a single flat colour")

    out = p.add_argument_group("output")
    out.add_argument("--out", help="output file (.png or .bmp); default capture.png")
    out.add_argument("--format", choices=("png", "bmp"), help="image format; default comes from the extension")
    out.add_argument("--series", type=int, default=0, metavar="N", help="take N shots and report LIVE or FROZEN")
    out.add_argument("--interval", type=float, default=1.0, metavar="SECONDS", help="time between series shots")
    out.add_argument("--min-changed", type=int, default=200, metavar="PIXELS",
                     help="pixels that must change between two shots to count as motion (default: 200)")
    out.add_argument("--out-dir", default="captures", help="directory for series shots (default: captures)")
    return p


def parse_crop(text: str) -> tuple[int, int, int, int]:
    try:
        x, y, w, h = (int(part) for part in text.split(","))
    except ValueError:
        raise ValueError("--crop needs four whole numbers: X,Y,W,H") from None
    if w <= 0 or h <= 0:
        raise ValueError("--crop width and height must be positive")
    return x, y, w, h


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.title is None and args.class_name is None and args.pid is None:
        build_parser().error("give --title, --class or --pid")
    if args.series == 1 or args.series < 0:
        build_parser().error("--series needs at least 2 shots")
    crop = None
    if args.crop:
        try:
            crop = parse_crop(args.crop)
        except ValueError as exc:
            build_parser().error(str(exc))

    from . import capture as cap

    try:
        if args.dpi_aware:
            cap.enable_dpi_awareness()
        if args.list:
            for w in cap.find_windows(title=args.title, class_name=args.class_name, pid=args.pid, match=args.match):
                print("0x%X pid=%d class=%r %dx%d title=%r" % (w.hwnd, w.pid, w.class_name, w.width, w.height, w.title))
            return EXIT_OK
        window = cap.find_window(
            title=args.title, class_name=args.class_name, pid=args.pid, match=args.match,
            prefer_largest=args.largest, timeout=args.wait,
        )
        print("window: hwnd=0x%X pid=%d title=%r" % (window.hwnd, window.pid, window.title))
        options = dict(
            client_only=args.client, fallback=args.fallback, full_content=not args.no_full_content,
            crop_to_frame=args.crop_frame, restore=args.restore,
        )

        def shoot():
            shot = cap.capture_window(window, **options)
            if crop:
                shot = shot.crop(*crop)
            if args.fail_on_blank and shot.is_blank():
                raise CaptureFailedError("capture is a single flat colour")
            return shot

        if args.series:
            return _series(args, cap, shoot)
        shot = shoot()
        path = shot.save(args.out or "capture.png", args.format)
        print("saved %s (%dx%d)" % (path, shot.width, shot.height))
        return EXIT_OK
    except UnsupportedPlatformError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_UNSUPPORTED
    except WindowNotFoundError as exc:
        print("WINDOW-NOT-FOUND: %s" % exc, file=sys.stderr)
        return EXIT_NOT_FOUND
    except (CaptureFailedError, WindowCaptureError, ValueError, OSError) as exc:
        print("CAPTURE-FAILED: %s" % exc, file=sys.stderr)
        return EXIT_CAPTURE_FAILED


def _series(args, cap, shoot) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = args.format or (Path(args.out).suffix.lstrip(".").lower() if args.out else "") or "png"
    result = cap.watch(shoot, args.series, args.interval, min_changed=args.min_changed)
    for i, shot in enumerate(result.captures):
        path = shot.save(out_dir / ("cap_%02d.%s" % (i, ext)), ext)
        print("shot %02d saved %s (%dx%d)" % (i, path, shot.width, shot.height))
    for i, changed in enumerate(result.changed, start=1):
        note = "(MOTION)" if changed >= args.min_changed else ""
        print("pair %02d->%02d: %d px changed %s" % (i - 1, i, changed, note))
    pairs = len(result.changed)
    if result.live:
        print("VERDICT: LIVE (%d/%d pairs show motion)" % (result.moving_pairs, pairs))
        return EXIT_OK
    print("VERDICT: FROZEN (0/%d pairs show motion)" % pairs)
    return EXIT_FROZEN


if __name__ == "__main__":
    sys.exit(main())
