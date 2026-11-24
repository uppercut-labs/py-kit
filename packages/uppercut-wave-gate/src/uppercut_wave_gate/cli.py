"""Command line: `uppercut-wave-gate` and the argument helpers a project's own wave command reuses."""

from __future__ import annotations

import argparse
import shlex
import sys
from typing import Any, Dict, List, Optional, Sequence

from ._files import WaveError
from ._runner import command_runner
from ._wave import Call, Runner, WaveConfig, parse_workers, run_wave

DESCRIPTION = "Run one prompt over many pieces; only output that passes the gate counts."
EPILOG = """\
pieces.jsonl: one {"id": "...", "text": "..."} per line. Each text is sent to the runner as the user message
and the prompt file is the system prompt. --worker is HOST=MODEL or HOST=MODEL*SLOTS.

The gate is a shell command with {out} replaced by the quoted output file and {id} by that file's name
without its extension. Exit 0 passes it; anything else fails it. The wave refuses to start unless the gate
rejects --bad, a known-bad output.

  run/results/<id>.out  passed the gate     run/failed/<id>.out  gave up (<id>.why says why)
  run/ledger.jsonl      every attempt       run/manifest.json    fingerprint of prompt, gate, workers, pieces
  run/summary.json      counts

Run the same command again to resume. Exit codes: 0 all passed, 2 usage, refused resume or gate probe,
3 finished with failures, 4 no workers left, 5 --budget reached, 6 --stop-after tripped.
"""


def add_wave_arguments(parser: argparse.ArgumentParser, **defaults: Any) -> argparse.ArgumentParser:
    """Add the options every wave command shares. Keyword arguments override the defaults of
    `timeout`, `gate_timeout`, `retries` and `stop_after`."""
    d = {"timeout": 120, "gate_timeout": 10, "retries": 2, "stop_after": 0, **defaults}
    parser.add_argument("--pieces", required=True)
    parser.add_argument("--prompt", required=True, help="file holding the system prompt")
    parser.add_argument("--out", required=True)
    parser.add_argument("--worker", action="append", required=True, help="HOST=MODEL or HOST=MODEL*SLOTS")
    parser.add_argument("--gate", required=True, help="shell command with {out}; exit 0 passes")
    parser.add_argument("--bad", required=True, help="known-bad output the gate must reject")
    parser.add_argument("--timeout", type=int, default=d["timeout"], help="seconds per call")
    parser.add_argument("--gate-timeout", type=int, default=d["gate_timeout"])
    parser.add_argument("--retries", type=int, default=d["retries"])
    parser.add_argument("--budget", type=int, help="stop starting pieces after this many seconds")
    parser.add_argument("--stop-after", type=int, default=d["stop_after"],
                        help="halt after this many gate failures in a row; 0 is off")
    parser.add_argument("--limit", type=int, help="run only the first N unfinished pieces, for a pilot")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--invalidate-changed", action="store_true",
                        help="invalidate and re-run pieces if prompt, piece content, or gate changed")
    parser.add_argument("--new-run", action="store_true",
                        help="require a fresh output directory (fails if out directory contains an existing run)")
    return parser


def config_from_args(args: argparse.Namespace, settings: Optional[Dict[str, Any]] = None) -> WaveConfig:
    """Build a WaveConfig from the options `add_wave_arguments` added. `settings` goes to the runner."""
    return WaveConfig(
        pieces=args.pieces, prompt=args.prompt, out=args.out, workers=parse_workers(args.worker),
        gate=args.gate, bad=args.bad, timeout=args.timeout, gate_timeout=args.gate_timeout,
        retries=args.retries, budget=args.budget, stop_after=args.stop_after, limit=args.limit,
        retry_failed=args.retry_failed, invalidate_changed=args.invalidate_changed, new_run=args.new_run,
        settings=dict(settings or {}),
    )


def main(argv: Optional[Sequence[str]] = None, runner: Optional[Runner] = None) -> int:
    """The command. Without `runner`, `--runner-cmd` is required and names the command that calls the model:
    it gets the piece text on standard input and prints the output. `{host}`, `{model}`, `{prompt_file}` and
    `{piece_id}` in its arguments are replaced; the command is split into words before the replacement, so a
    value is never re-parsed."""
    parser = argparse.ArgumentParser(prog="uppercut-wave-gate", description=DESCRIPTION, epilog=EPILOG,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_wave_arguments(parser)
    parser.add_argument("--runner-cmd", help="command that runs one piece (standard input in, output on standard out)")
    args = parser.parse_args(argv)
    try:
        cfg = config_from_args(args)
        if runner is None:
            if not args.runner_cmd:
                raise WaveError("--runner-cmd is required (or pass a runner to main())")
            words: List[str] = shlex.split(args.runner_cmd, posix=sys.platform != "win32")

            def build(call: Call) -> List[str]:
                values = {"host": call.worker.host, "model": call.worker.model, "prompt_file": cfg.prompt,
                          "piece_id": call.piece_id}
                return [w.replace("{host}", values["host"]).replace("{model}", values["model"])
                        .replace("{prompt_file}", values["prompt_file"]).replace("{piece_id}", values["piece_id"])
                        for w in words]

            runner = command_runner(build)
        return run_wave(cfg, runner)
    except WaveError as exc:
        sys.stderr.write("wave: %s\n" % exc)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
