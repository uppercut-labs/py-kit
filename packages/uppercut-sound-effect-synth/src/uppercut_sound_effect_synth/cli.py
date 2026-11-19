"""Command line: list sounds or render a pack of WAV files."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .pack import render_pack
from .recipes import SOUNDS


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="uppercut-sound-effect-synth",
        description="Render deterministic procedural sound effects to WAV files.",
    )
    parser.add_argument("--out", help="output folder")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--take", type=int, default=0, help="variation number; the same take is always identical")
    parser.add_argument("--only", help="comma-separated sound names")
    parser.add_argument("--bits", type=int, choices=[8, 16, 24], default=24)
    parser.add_argument("--mono", action="store_true")
    parser.add_argument("--preview", action="store_true", help="also write _preview.wav with every sound in a row")
    parser.add_argument("--list", action="store_true", help="print the built-in sound names and exit")
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(SOUNDS))
        return 0
    if not args.out:
        parser.error("--out is required unless --list is given")
    names = [n.strip() for n in args.only.split(",") if n.strip()] if args.only else None
    try:
        manifest = render_pack(
            args.out, names, args.seed, args.take, bits=args.bits, channels=1 if args.mono else 2, preview=args.preview
        )
    except KeyError as error:
        print(f"error: {error.args[0]}", file=sys.stderr)
        return 2
    for name, entry in manifest["sounds"].items():  # type: ignore[union-attr]
        print(f"{name:13} {entry['seconds']:5.2f}s  {entry['sha256'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
