"""Command line: detect, grid, align and bed."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .align import align
from .bed import STYLES, SCALES, generate_bed, parse_sections, write_bed
from .detect import detect, grid_from_detection
from .grid import load_grid, make_grid, save_grid


def _read_json(path: str) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="uppercut-beat-grid", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("detect", help="detect tempo, beats and downbeats in an audio file")
    d.add_argument("audio")
    d.add_argument("--backend", choices=["fit", "librosa"], default="fit")
    d.add_argument("--bpm-hint", type=float)
    d.add_argument("--fps", type=float, default=60.0)
    d.add_argument("--json", help="write the grid (with frame indices) to this path")

    g = sub.add_parser("grid", help="make a grid for a known tempo")
    g.add_argument("--bpm", type=float, required=True)
    g.add_argument("--duration", type=float, required=True)
    g.add_argument("--offset", type=float, default=0.0)
    g.add_argument("--fps", type=float, default=60.0)
    g.add_argument("--json")

    a = sub.add_parser("align", help="check cuts against a grid; exit 1 when any cut misses")
    a.add_argument("--cuts", required=True, help='JSON: a list, or {"cuts": [...]}')
    a.add_argument("--beats", required=True, help="grid JSON from detect or grid")
    a.add_argument("--tolerance-ms", type=float, default=17.0)
    a.add_argument("--half", action="store_true")
    a.add_argument("--auto-offset", action="store_true")

    b = sub.add_parser("bed", help="render a beat-synced music bed")
    b.add_argument("--bpm", type=float, required=True)
    b.add_argument("--sections", required=True, help="name:bars pairs, e.g. intro:2,build:4,drop:4")
    b.add_argument("--out", required=True, help="output base path (no extension)")
    b.add_argument("--style", choices=list(STYLES), default="pulse")
    b.add_argument("--key", default="A")
    b.add_argument("--scale", choices=list(SCALES), default="minor")
    b.add_argument("--seed", type=int, default=3)
    b.add_argument("--fps", type=float, default=60.0)
    b.add_argument("--end-hit", action="store_true")
    b.add_argument("--gaps", default="", help="comma-separated beat indices preceded by half a beat of silence")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "detect":
            result = detect(args.audio, backend=args.backend, bpm_hint=args.bpm_hint)
            print(
                f"tempo {result['tempo']} BPM, {len(result['beats'])} beats, "
                f"downbeat phase {result['downbeat_phase']}, {len(result['onsets'])} onsets"
            )
            if args.json:
                save_grid({**grid_from_detection(result, args.fps), "audio": result["audio"],
                           "energy": result["energy"], "onsets": result["onsets"]}, args.json)
            return 0
        if args.cmd == "grid":
            grid = make_grid(args.bpm, args.duration, args.offset, args.fps)
            print(f"{args.bpm} BPM, {grid['frames_per_beat']} frames/beat at {args.fps} fps, {len(grid['beats'])} beats")
            if args.json:
                save_grid(grid, args.json)
            return 0
        if args.cmd == "align":
            report = align(_read_json(args.cuts), load_grid(args.beats), args.tolerance_ms, args.half, args.auto_offset)  # type: ignore[arg-type]
            if args.auto_offset:
                print(f"global offset removed: {report.offset_ms:+.1f} ms")
            for r in report.results:
                print(f"cut {r.cut:8.3f}s  delta {r.delta_ms:+7.1f} ms  residual {r.residual_ms:+7.1f} ms  {'ok' if r.ok else 'MISS'}")
            print(f"{len(report.results) - report.misses}/{len(report.results)} cuts within {args.tolerance_ms} ms")
            return 0 if report.all_ok else 1
        gaps = [int(x) for x in args.gaps.split(",") if x.strip()]
        bed = generate_bed(args.bpm, parse_sections(args.sections), args.style, args.key, args.scale,
                           args.seed, args.fps, args.end_hit, gaps)
        paths = write_bed(bed, args.out)
        print(f"{args.bpm} BPM, {len(bed.grid['beats'])} beats -> {paths['mix']}, {paths['grid']}")
        return 0
    except (ValueError, ImportError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
