"""Command line: measure files or normalize one."""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Sequence

from .core import (
    DEFAULT_TARGET_LUFS,
    DEFAULT_TRUE_PEAK_DBTP,
    LoudnessError,
    Measure,
    measure,
    normalize,
)


def _finite(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _record(path: str, m: Measure) -> dict[str, object]:
    return {
        "file": path,
        "integrated_lufs": _finite(m.integrated),
        "true_peak_dbtp": _finite(m.true_peak),
        "lra": None if m.lra is None else _finite(m.lra),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="uppercut-ffmpeg-loudness",
        description="Measure loudness with ffmpeg, or normalize a file to a target.",
    )
    parser.add_argument("files", nargs="+", help="files to measure; with --normalize, the source")
    parser.add_argument("--target", type=float, help="target integrated LUFS")
    parser.add_argument("--tp", type=float, default=DEFAULT_TRUE_PEAK_DBTP, help="true-peak ceiling, dBTP")
    parser.add_argument("--normalize", metavar="OUT", help="write a normalized copy of the one source file")
    parser.add_argument("--mode", choices=["gain", "loudnorm"], default="gain")
    parser.add_argument("--json", action="store_true", help="print JSON records")
    args = parser.parse_args(argv)
    try:
        if args.normalize:
            if len(args.files) != 1:
                parser.error("--normalize takes exactly one source file")
            target = DEFAULT_TARGET_LUFS if args.target is None else args.target
            result = normalize(args.files[0], args.normalize, target, args.tp, mode=args.mode)
            records = [_record(args.normalize, result.after)]
            records[0]["gain_db"] = result.gain_db
            records[0]["limited_by"] = result.limited_by
            records[0]["reached_target"] = result.reached_target
            failed = not result.reached_target
        else:
            measures = [(f, measure(f)) for f in args.files]
            records = [_record(f, m) for f, m in measures]
            failed = False
            if args.target is not None:
                failed = any(
                    abs(m.integrated - args.target) > 1.0 or m.true_peak > args.tp for _, m in measures
                )
    except LoudnessError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(records, indent=2, allow_nan=False))
    else:
        for r in records:
            print(f"{r['file']}: {r['integrated_lufs']} LUFS, {r['true_peak_dbtp']} dBTP, LRA {r['lra']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
