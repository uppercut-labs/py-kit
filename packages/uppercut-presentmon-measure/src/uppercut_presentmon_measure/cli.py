"""Command line: ``run`` scenarios from a config or arguments, or ``summarize`` an existing CSV."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .config import ConfigError, Scenario, load_config, parse_scenario
from .parse import FrameParseError, parse_presentmon_csv, summarize_frames
from .runner import resolve_presentmon, run_scenario


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uppercut-presentmon-measure", description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    run = sub.add_parser("run", help="run scenarios and write a JSON summary")
    run.add_argument("--config", type=Path, help="JSON config file with a scenarios list")
    run.add_argument("--presentmon", help="path to the PresentMon executable (or set PRESENTMON_CLI)")
    run.add_argument("--out", type=Path, help="output folder (default: out_dir from the config, or out)")
    run.add_argument("--scenario", action="append", help="run only this scenario name (repeatable)")
    run.add_argument("--kind", choices=("startup", "idle", "workload"), help="single scenario from arguments")
    run.add_argument("--name", help="name of the single scenario")
    run.add_argument("--cwd", help="working directory of the app")
    run.add_argument("--runs", type=int, help="launches for a startup scenario")
    run.add_argument("--warmup", type=float, help="seconds after launch before the capture starts")
    run.add_argument("--capture", type=float, help="seconds of capture")
    run.add_argument("--marks-file", help="file the app writes its startup marks to")
    run.add_argument("--capture-process-name", help="capture this process name instead of the launched pid")
    run.add_argument("--refresh-hz", type=float, help="display refresh rate, adds missedRefreshFrames")
    run.add_argument("--workload-command", help="workload command line (split on spaces)")
    run.add_argument("app", nargs="*", help="app command after --, for a single scenario")

    summ = sub.add_parser("summarize", help="summarize a PresentMon CSV file to JSON")
    summ.add_argument("csv", type=Path)
    summ.add_argument("--refresh-hz", type=float)
    return parser


def _scenario_from_args(args: argparse.Namespace) -> Scenario:
    data: dict = {"kind": args.kind, "command": list(args.app)}
    for key, value in (
        ("name", args.name), ("cwd", args.cwd), ("runs", args.runs),
        ("warmup_seconds", args.warmup), ("capture_seconds", args.capture),
        ("marks_file", args.marks_file), ("capture_process_name", args.capture_process_name),
        ("refresh_hz", args.refresh_hz),
    ):
        if value is not None:
            data[key] = value
    if args.workload_command:
        data["workload_command"] = args.workload_command.split()
    return parse_scenario(data)


def _run(args: argparse.Namespace) -> int:
    if args.config and args.kind:
        raise ConfigError("use either --config or --kind with an app command")
    if args.config:
        config = load_config(args.config)
        scenarios, presentmon_option = config.scenarios, args.presentmon or config.presentmon
        extra, out_dir = config.presentmon_args, args.out or Path(config.out_dir)
    elif args.kind:
        scenarios, presentmon_option = [_scenario_from_args(args)], args.presentmon
        extra, out_dir = [], args.out or Path("out")
    else:
        raise ConfigError("give --config, or --kind with the app command after --")
    if args.scenario:
        unknown = set(args.scenario) - {s.name for s in scenarios}
        if unknown:
            raise ConfigError(f"unknown scenario: {', '.join(sorted(unknown))}")
        scenarios = [s for s in scenarios if s.name in args.scenario]
    if sys.platform != "win32":
        raise RuntimeError("running scenarios needs Windows (PresentMon is a Windows tool)")
    presentmon = None
    if any(s.kind != "startup" for s in scenarios):
        presentmon = resolve_presentmon(presentmon_option)
    results = [run_scenario(s, presentmon, out_dir, extra) for s in scenarios]
    out_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"scenarios": results}, indent=2)
    (out_dir / "result.json").write_text(text, encoding="utf-8")
    print(text)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.action == "summarize":
            frames = parse_presentmon_csv(args.csv.read_text(encoding="utf-8"))
            print(json.dumps(summarize_frames(frames, args.refresh_hz), indent=2))
            return 0
        return _run(args)
    except (ConfigError, FrameParseError, FileNotFoundError, RuntimeError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
