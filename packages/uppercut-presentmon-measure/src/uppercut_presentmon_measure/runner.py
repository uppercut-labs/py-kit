"""Launch an app, capture frames with PresentMon, return a summary dict.

Launching is Windows-only: ``run_scenario`` raises ``RuntimeError`` elsewhere unless
``allow_any_platform=True`` (used by tests with a stand-in capture tool). Everything it summarizes
goes through the pure functions in ``parse``.
"""
from __future__ import annotations

import os
import re
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
    summarize_displayed,
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


def list_processes() -> list[tuple[int, int, str]]:
    """All running processes as ``(pid, parent_pid, command_line)``.

    On Windows this asks PowerShell (``Get-CimInstance Win32_Process``); elsewhere it uses ``ps``.
    """
    if sys.platform == "win32":
        script = "Get-CimInstance Win32_Process | ForEach-Object { '{0}|{1}|{2}' -f $_.ProcessId, $_.ParentProcessId, $_.CommandLine }"
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, errors="replace", timeout=120,
        )
        rows = (line.split("|", 2) for line in done.stdout.splitlines() if line.count("|") >= 2)
        return [(int(p), int(pp), cmd) for p, pp, cmd in rows if p.isdigit() and pp.isdigit()]
    done = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, errors="replace", timeout=30,
    )
    found = (re.match(r"\s*(\d+)\s+(\d+)\s+(.*)$", line) for line in done.stdout.splitlines())
    return [(int(m.group(1)), int(m.group(2)), m.group(3)) for m in found if m]


def find_descendant_pid(
    root_pid: int,
    command_line_contains: str,
    processes: Sequence[tuple[int, int, str]] | None = None,
    timeout_seconds: float = 0.0,
    poll_seconds: float = 0.25,
) -> int | None:
    """Find a child, grandchild and so on of ``root_pid`` whose command line contains the text.

    Use it for apps that present from a helper process: Chromium and Electron present from the
    process started with ``--type=gpu-process``. Returns the lowest matching pid, or None. The root
    itself is not a candidate. ``processes`` is a ``(pid, parent_pid, command_line)`` listing to
    search, default ``list_processes()``; with the default listing the search repeats for up to
    ``timeout_seconds`` while nothing matches, since a helper may start after the app does.
    """
    if not command_line_contains:
        raise ValueError("command_line_contains must not be empty")
    deadline = time.time() + timeout_seconds
    while True:
        listing = list_processes() if processes is None else processes
        children: dict[int, list[int]] = {}
        for pid, parent, _ in listing:
            children.setdefault(parent, []).append(pid)
        command = {pid: cmd for pid, _, cmd in listing}
        seen, queue, matches = {root_pid}, [root_pid], []
        while queue:
            for child in children.get(queue.pop(), []):
                if child in seen:
                    continue
                seen.add(child)
                queue.append(child)
                if command_line_contains in command.get(child, ""):
                    matches.append(child)
        if matches:
            return min(matches)
        if processes is not None or time.time() >= deadline:
            return None
        time.sleep(poll_seconds)


def _start_capture(
    presentmon: "str | Sequence[str]", csv_path: Path, seconds: float,
    pid: int | None, process_name: str | None, extra_args: Sequence[str],
) -> subprocess.Popen:
    if csv_path.exists():
        csv_path.unlink()
    command = build_presentmon_command(
        presentmon, csv_path, seconds, pid=pid, process_name=process_name, extra_args=extra_args,
    )
    return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _summarize_capture(csv_path: Path, since: float, refresh_hz: float | None) -> dict:
    result: dict = {}
    written = find_capture_csv(csv_path, since)
    result["csv"] = str(written or csv_path)
    try:
        frames = parse_presentmon_csv(written.read_text(encoding="utf-8")) if written else []
        result["frames"] = summarize_frames(frames, refresh_hz)
        if refresh_hz and any(f["display_ms"] is not None for f in frames):
            result["displayed"] = summarize_displayed(frames, refresh_hz)
        if not frames:
            result["note"] = "the capture recorded no frames"
    except FrameParseError as error:
        result["frames"] = {"frames": 0}
        result["note"] = str(error)
    return result


def capture_frames(
    presentmon: "str | Sequence[str]",
    out_csv: str | Path,
    seconds: float,
    pid: int | None = None,
    process_name: str | None = None,
    delay_seconds: float = 0.0,
    extra_args: Sequence[str] = (),
    refresh_hz: float | None = None,
) -> dict:
    """Capture one process with PresentMon and summarize it. Blocks until the capture ends.

    Waits ``delay_seconds``, captures ``pid`` (or ``process_name``) for ``seconds``, finds the CSV the
    capture wrote, and returns ``{"presentmonExitCode", "csv", "frames", ...}`` like a scenario does:
    ``frames`` from ``summarize_frames``, ``displayed`` from ``summarize_displayed`` when
    ``refresh_hz`` is given and the CSV has a display-change column, and a ``note`` for an empty
    capture. Use it when your own script owns the timeline and the scenario runner does not fit.
    Intel's PresentMon service records nothing from a window that is idle, so start the capture once
    motion has begun, and capture a process once per launch.
    """
    csv_path = Path(out_csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    since = time.time() - 1.0
    capture = _start_capture(presentmon, csv_path, seconds, pid, process_name, extra_args)
    try:
        exit_code = capture.wait(timeout=seconds + 60)
    finally:
        _stop(capture)
    result: dict = {"presentmonExitCode": exit_code}
    if pid is not None:
        result["capturedPid"] = pid
    return {**result, **_summarize_capture(csv_path, since, refresh_hz)}


def _run_capture(scenario: Scenario, presentmon: "str | Sequence[str]", out_dir: Path, extra_args: Sequence[str]) -> dict:
    csv_path = out_dir / f"{scenario.name}.csv"
    capture_started = time.time() - 1.0
    if scenario.marks_file and Path(scenario.marks_file).exists():
        Path(scenario.marks_file).unlink()
    proc, started = _launch(scenario)
    result: dict = {"launchedPid": proc.pid}
    delay = scenario.capture_delay_seconds
    workload_proc: subprocess.Popen | None = None
    capture: subprocess.Popen | None = None
    limit = scenario.capture_seconds + delay + 60
    try:
        time.sleep(scenario.warmup_seconds)
        if proc.poll() is not None:
            raise RuntimeError(f"the app exited with code {proc.returncode} before the capture started")
        if delay > 0 and scenario.kind == "workload" and scenario.workload_command:
            # The capture starts after the workload does, so the workload runs alongside the wait.
            workload_proc = subprocess.Popen(
                scenario.workload_command, cwd=scenario.cwd, env={**os.environ, **scenario.env},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        if delay > 0:
            time.sleep(delay)
            if proc.poll() is not None:
                raise RuntimeError(f"the app exited with code {proc.returncode} before the capture started")
        pid: int | None = None if scenario.capture_process_name else proc.pid
        if scenario.capture_child_match:
            pid = find_descendant_pid(proc.pid, scenario.capture_child_match, timeout_seconds=10.0)
            if pid is None:
                raise RuntimeError(
                    f"no child of the app (pid {proc.pid}) has a command line containing {scenario.capture_child_match!r}"
                )
        if pid is not None:
            result["capturedPid"] = pid
        capture = _start_capture(presentmon, csv_path, scenario.capture_seconds, pid, scenario.capture_process_name, extra_args)
        if scenario.kind == "workload" and scenario.workload_command:
            if workload_proc is None:
                work = subprocess.run(
                    scenario.workload_command, cwd=scenario.cwd, env={**os.environ, **scenario.env},
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=limit,
                )
                result["workloadExitCode"] = work.returncode
            else:
                result["workloadExitCode"] = workload_proc.wait(timeout=limit)
        result["presentmonExitCode"] = capture.wait(timeout=limit)
    finally:
        for child in (workload_proc, capture):
            if child is not None:
                _stop(child)
        _stop(proc)
    result.update(_summarize_capture(csv_path, capture_started, scenario.refresh_hz))
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
