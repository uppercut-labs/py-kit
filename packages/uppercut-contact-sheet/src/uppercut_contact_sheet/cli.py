"""Command line entry point: ``uppercut-contact-sheet``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .sheet import IMAGE_SUFFIXES, contact_sheet, save_image, save_paged, tiles_from_paths
from .video import frame_times, parse_time, probe_duration, video_tiles


def _parse_cell(text: str) -> tuple[int, int]:
    try:
        w, h = text.lower().split("x")
        return int(w), int(h)
    except ValueError:
        raise argparse.ArgumentTypeError("cell must look like 320x200") from None


def _expand(inputs: list[str]) -> list[Path]:
    paths: list[Path] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            paths.extend(sorted(c for c in p.iterdir() if c.suffix.lower() in IMAGE_SUFFIXES))
        else:
            paths.append(p)
    return paths


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="uppercut-contact-sheet",
        description="Build a labelled contact sheet from images, folders of images, or video frames.",
    )
    ap.add_argument("inputs", nargs="*", help="image files or folders of images")
    ap.add_argument("-o", "--out", required=True, help="output file, or the prefix when --per-sheet is set")
    ap.add_argument("--video", help="grab frames from this video instead of reading images (needs ffmpeg)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--every", type=float, help="video: one frame every N seconds")
    g.add_argument("--count", type=int, help="video: N evenly spaced frames")
    g.add_argument("--at", help="video: comma-separated timestamps (seconds or mm:ss)")
    ap.add_argument("--start", type=float, default=0.0, help="video: first second to sample")
    ap.add_argument("--end", type=float, help="video: last second to sample")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--cell", type=_parse_cell, default=(320, 200), help="picture area, WxH (default 320x200)")
    ap.add_argument("--fit", choices=["thumbnail", "contain", "nearest"], default="thumbnail")
    ap.add_argument("--bg", default="#111419")
    ap.add_argument("--fg", default="white")
    ap.add_argument("--caption", choices=["below", "above", "none"], default="below")
    ap.add_argument("--pad", type=int, default=8)
    ap.add_argument("--title")
    ap.add_argument("--border", help="outline color for each cell")
    ap.add_argument("--font", action="append", default=[], help="font file to try first (repeatable)")
    ap.add_argument("--per-sheet", type=int, help="split into numbered sheets of this many tiles")
    ap.add_argument("--quality", type=int, default=88, help="JPEG quality")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from .sheet import load_font

    try:
        if args.video:
            dur = probe_duration(args.video)
            if args.at:
                times = [parse_time(x) for x in args.at.split(",") if x.strip()]
            elif args.every or args.count:
                times = frame_times(dur, every=args.every, count=args.count, start=args.start, end=args.end)
            else:
                raise ValueError("with --video give --every, --count or --at")
            tiles = video_tiles(args.video, times, width=args.cell[0])
        else:
            if not args.inputs:
                raise ValueError("give image files or folders, or use --video")
            tiles = tiles_from_paths(_expand(args.inputs))
            if not tiles:
                raise ValueError("no images found")

        font = load_font(max(12, args.cell[0] // 24), args.font) if args.font else None
        opts = dict(cols=args.cols, cell=args.cell, fit=args.fit, bg=args.bg, fg=args.fg,
                    caption=args.caption, pad=args.pad, border=args.border, font=font)
        if args.per_sheet:
            out = Path(args.out)
            paths = save_paged(tiles, out.with_suffix(""), args.per_sheet, ext=out.suffix.lstrip(".") or "jpg",
                               quality=args.quality, title=args.title, **opts)
        else:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            paths = [save_image(contact_sheet(tiles, title=args.title, **opts), out, quality=args.quality)]
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"uppercut-contact-sheet: {exc}", file=sys.stderr)
        return 1
    print("\n".join(str(p) for p in paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
