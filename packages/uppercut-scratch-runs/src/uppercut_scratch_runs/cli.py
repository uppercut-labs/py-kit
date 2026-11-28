"""Command line: ``new``, ``run -- <command>`` and ``clean <path>``."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from ._config import ConfigError, ScratchConfig, load_table

PROG = "uppercut-scratch-runs"


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, metavar="FILE",
                        help="TOML file with a [tool.uppercut-scratch-runs] table "
                             "(default: the nearest pyproject.toml that has one)")
    common.add_argument("--project", help="project name; the marker defaults to .<project>-run")
    common.add_argument("--marker", help="ownership marker file name")
    common.add_argument("--variable", help="extra environment variable that `run` sets to the run path")
    common.add_argument("--root", dest="roots", action="append", type=Path, metavar="DIR",
                        help="run root to use instead of the configured ones (repeat for more; "
                             "the last is the large one)")
    sized = argparse.ArgumentParser(add_help=False)
    sized.add_argument("--large", action="store_true", help="use the last root listed for this platform")

    parser = argparse.ArgumentParser(prog=PROG, description="Create and remove disposable run directories.")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("new", parents=[common, sized], help="allocate a run and print its absolute path")
    job = actions.add_parser("run", parents=[common, sized],
                             help="run a command in a new run directory, then remove it")
    job.add_argument("command", nargs=argparse.REMAINDER, help="-- followed by the command")
    remove = actions.add_parser("clean", parents=[common], help="remove one run made by new")
    remove.add_argument("path", type=Path)
    return parser


def _settings(args: argparse.Namespace) -> ScratchConfig:
    _, table = load_table(args.config)
    for key in ("project", "marker", "variable"):
        value = getattr(args, key)
        if value is not None:
            table[key] = value
    if args.roots:
        table["roots"] = {"default": [str(root) for root in args.roots]}
    config = ScratchConfig.from_mapping(table)
    config.platform_roots()  # Fail before doing anything when no root applies.
    return config


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    command: list[str] = []
    if args.action == "run":
        command = args.command[1:] if args.command[:1] == ["--"] else list(args.command)
        if not command:
            parser.error("run requires a command after --")
    try:
        config = _settings(args)
        if args.action == "clean":
            config.clean(args.path)
            return 0
        run = config.new(large=args.large)
        if args.action == "new":
            print(run)
            return 0
        print(f"Scratch: {run}", file=sys.stderr, flush=True)
        try:
            status = subprocess.run(command, env=config.environment(run)).returncode
        finally:
            config.clean(run)
    except (ConfigError, OSError, ValueError) as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1
    # A command killed by a signal reports -N; a shell would report 128 + N.
    return 128 - status if status < 0 else status


def entry() -> None:
    sys.exit(main())
