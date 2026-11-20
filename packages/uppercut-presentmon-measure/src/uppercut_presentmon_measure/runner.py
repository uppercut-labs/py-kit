"""Launch an app, capture frames with PresentMon, return a summary dict.

Launching is Windows-only: ``run_scenario`` raises ``RuntimeError`` elsewhere unless
``allow_any_platform=True`` (used by tests with a stand-in capture tool). Everything it summarizes
goes through the pure functions in ``parse``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping, Sequence

from .config import Scenario
from .parse import (
    FrameParseError,
    parse_marks,
    parse_presentmon_csv,
    summarize_frames,
    summarize_runs,
    summarize_startup_marks,
)

ENV_PRESENTMON = "PRESENTMON_CLI"
_CANDIDATE_NAMES = ("PresentMonCli", "PresentMonCli.exe", "PresentMon", "PresentMon.exe")


def resolve_presentmon(option: str | None = None, env: Mapping[str, str] | None = None) -> str:
    """Find the PresentMon executable: the option, then ``PRESENTMON_CLI``, then ``PATH``.

    Raises FileNotFoundError naming where it looked.
    """
    environment = os.environ if env is None else env
    for source, value in (("option", option), (ENV_PRESENTMON, environment.get(ENV_PRESENTMON))):
        if value:
            if Path(value).is_file():
                return value
            raise FileNotFoundError(f"PresentMon from {source} not found: {value}")
    for name in _CANDIDATE_NAMES:
        found = shutil.which(name, path=environment.get("PATH"))
        if found:
            return found
    raise FileNotFoundError(f"PresentMon not found; pass its path or set {ENV_PRESENTMON}")


def build_presentmon_command(
    executable: "str | Sequence[str]",
    output_file: str | Path,
    seconds: float,
    pid: int | None = None,
    process_name: str | None = None,
    extra_args: Sequence[str] = (),
) -> list[str]:
    """Build the capture command line. Exactly one of ``pid`` and ``process_name`` is required."""
    if (pid is None) == (process_name is None):
        raise ValueError("give exactly one of pid and process_name")
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    command = [executable] if isinstance(executable, str) else list(executable)
    command += ["--process_id", str(pid)] if pid is not None else ["--process_name", str(process_name)]
    command += ["--output_file", str(output_file), "--timed", f"{seconds:g}"]
    return command + list(extra_args)


def _stop(proc: subprocess.Popen) -> None:
    """Stop the process we started, by its own handle."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def _launch(scenario: Scenario) -> tuple[subprocess.Popen, float]:
    env = {**os.environ, **scenario.env}
    started = time.time() * 1000.0
    proc = subprocess.Popen(
        scenario.command, cwd=scenario.cwd, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc, started


def _read_marks(path: str | None) -> dict[str, float]:
    if not path:
        return {}
    try:
        return parse_marks(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return {}


def _wait_for_marks(path: str, timeout: float, proc: subprocess.Popen) -> dict[str, float]:
    end = time.time() + timeout
    while time.time() < end:
        marks = _read_marks(path)
        if marks:
            return marks
        if proc.poll() is not None:
            return _read_marks(path)
        time.sleep(0.02)
    return {}


def _run_startup(scenario: Scenario) -> dict:
    per_run: list[dict] = []
    for _ in range(scenario.runs):
        marks_path = Path(str(scenario.marks_file))
        if marks_path.exists():
            marks_path.unlink()
        proc, started = _launch(scenario)
        try:
            marks = _wait_for_marks(str(marks_path), scenario.marks_timeout_seconds, proc)
        finally:
            _stop(proc)
        per_run.append(summarize_startup_marks(marks, started))
    names = sorted({name for run in per_run for name in run})
    return {
        "runs": per_run,
        "marks": {name: summarize_runs([run.get(name) for run in per_run]) for name in names},
    }


def find_capture_csv(requested: str | Path, since: float = 0.0) -> Path | None:
    """Find the CSV a capture wrote. Intel's PresentMon service CLI appends
    ``-<process>[<pid>]-<timestamp>`` to the requested name, so ``out.csv`` arrives as
    ``out-app.exe[1234]-260101T120000.csv``. Returns the exact file if it exists, else the
    newest suffixed file modified at or after ``since`` (seconds since the epoch)."""
    requested = Path(requested)
    if requested.exists():
        return requested
    matches = [
        path for path in requested.parent.glob(f"{requested.stem}-*{requested.suffix}")
        if path.stat().st_mtime >= since
    ]
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def _run_capture(scenario: Scenario, presentmon: "str | Sequence[str]", out_dir: Path, extra_args: Sequence[str]) -> dict:
    csv_path = out_dir / f"{scenario.name}.csv"
    if csv_path.exists():
        csv_path.unlink()
    capture_started = time.time() - 1.0
    if scenario.marks_file and Path(scenario.marks_file).exists():
        Path(scenario.marks_file).unlink()
    proc, started = _launch(scenario)
    result: dict = {"launchedPid": proc.pid}
    try:
        time.sleep(scenario.warmup_seconds)
        if proc.poll() is not None:
            raise RuntimeError(f"the app exited with code {proc.returncode} before the capture started")
        command = build_presentmon_command(
            presentmon, csv_path, scenario.capture_seconds,
            pid=None if scenario.capture_process_name else proc.pid,
            process_name=scenario.capture_process_name,
            extra_args=extra_args,
        )
        limit = scenario.capture_seconds + 60
        capture = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            if scenario.kind == "workload" and scenario.workload_command:
                work = subprocess.run(
                    scenario.workload_command, cwd=scenario.cwd, env={**os.environ, **scenario.env},
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=limit,
                )
                result["workloadExitCode"] = work.returncode
            result["presentmonExitCode"] = capture.wait(timeout=limit)
        finally:
            _stop(capture)
    finally:
        _stop(proc)
    written = find_capture_csv(csv_path, capture_started)
    result["csv"] = str(written or csv_path)
    try:
        frames = parse_presentmon_csv(written.read_text(encoding="utf-8")) if written else []
        result["frames"] = summarize_frames(frames, scenario.refresh_hz)
        if not frames:
            result["note"] = "the capture recorded no frames"
    except FrameParseError as error:
        result["frames"] = {"frames": 0}
        result["note"] = str(error)
    marks = summarize_startup_marks(_read_marks(scenario.marks_file), started)
    if marks:
        result["marksMs"] = marks
    return result


def run_scenario(
    scenario: Scenario,
    presentmon: "str | Sequence[str] | None",
    out_dir: str | Path,
    extra_args: Sequence[str] = (),
    allow_any_platform: bool = False,
) -> dict:
    """Run one scenario and return its summary (also JSON-serializable).

    ``presentmon`` is the executable path (or an argv prefix); it is not needed for ``startup``.
    Raises RuntimeError off Windows unless ``allow_any_platform`` is set.
    """
    if sys.platform != "win32" and not allow_any_platform:
        raise RuntimeError("running scenarios needs Windows (PresentMon is a Windows tool)")
    if scenario.kind != "startup" and not presentmon:
        raise ValueError("a PresentMon executable is required for idle and workload scenarios")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    body = _run_startup(scenario) if scenario.kind == "startup" else _run_capture(scenario, presentmon, out, extra_args)  # type: ignore[arg-type]
    return {"name": scenario.name, "kind": scenario.kind, **body}
