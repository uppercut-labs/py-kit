"""Scenario configuration, from a JSON file or built in code."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

KINDS = ("startup", "idle", "workload")


class ConfigError(ValueError):
    """The configuration is missing a field or has a value of the wrong kind."""


@dataclass
class Scenario:
    """One measurement.

    ``startup`` launches the app ``runs`` times and reads marks; ``idle`` launches once and captures
    frames while nothing happens; ``workload`` does the same and also runs ``workload_command``
    when the capture starts.
    """

    name: str
    kind: str
    command: list[str]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    runs: int = 1
    warmup_seconds: float = 5.0
    capture_seconds: float = 10.0
    workload_command: list[str] | None = None
    marks_file: str | None = None
    marks_timeout_seconds: float = 30.0
    capture_process_name: str | None = None
    refresh_hz: float | None = None


@dataclass
class Config:
    presentmon: str | None
    scenarios: list[Scenario]
    out_dir: str = "out"
    presentmon_args: list[str] = field(default_factory=list)


def _str_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where} must be a non-empty list of strings")
    return list(value)


def _number(data: Mapping, key: str, default: float, where: str, minimum: float = 0.0) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        raise ConfigError(f"{where}.{key} must be a number >= {minimum}")
    return float(value)


def parse_scenario(data: Mapping, index: int = 0) -> Scenario:
    where = f"scenarios[{index}]"
    if not isinstance(data, Mapping):
        raise ConfigError(f"{where} must be an object")
    kind = data.get("kind")
    if kind not in KINDS:
        raise ConfigError(f"{where}.kind must be one of {', '.join(KINDS)}")
    name = data.get("name") or kind
    if not isinstance(name, str):
        raise ConfigError(f"{where}.name must be a string")
    command = _str_list(data.get("command"), f"{where}.command")
    workload = data.get("workload_command")
    if workload is not None:
        workload = _str_list(workload, f"{where}.workload_command")
    if kind == "workload" and workload is None:
        raise ConfigError(f"{where}.workload_command is required for a workload scenario")
    env = data.get("env", {})
    if not isinstance(env, Mapping) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise ConfigError(f"{where}.env must map strings to strings")
    runs = data.get("runs", 3 if kind == "startup" else 1)
    if isinstance(runs, bool) or not isinstance(runs, int) or runs < 1:
        raise ConfigError(f"{where}.runs must be an integer >= 1")
    marks_file = data.get("marks_file")
    if kind == "startup" and not marks_file:
        raise ConfigError(f"{where}.marks_file is required for a startup scenario")
    hz = data.get("refresh_hz")
    if hz is not None and (isinstance(hz, bool) or not isinstance(hz, (int, float)) or hz <= 0):
        raise ConfigError(f"{where}.refresh_hz must be a positive number")
    return Scenario(
        name=name,
        kind=kind,
        command=command,
        cwd=data.get("cwd"),
        env=dict(env),
        runs=runs,
        warmup_seconds=_number(data, "warmup_seconds", 5.0, where),
        capture_seconds=_number(data, "capture_seconds", 10.0, where, minimum=0.1),
        workload_command=workload,
        marks_file=marks_file,
        marks_timeout_seconds=_number(data, "marks_timeout_seconds", 30.0, where, minimum=0.1),
        capture_process_name=data.get("capture_process_name"),
        refresh_hz=float(hz) if hz is not None else None,
    )


def parse_config(data: Mapping) -> Config:
    """Validate a config mapping (the parsed JSON) and return a Config."""
    if not isinstance(data, Mapping):
        raise ConfigError("config must be a JSON object")
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ConfigError("scenarios must be a non-empty list")
    parsed = [parse_scenario(s, i) for i, s in enumerate(scenarios)]
    names = [s.name for s in parsed]
    if len(set(names)) != len(names):
        raise ConfigError("scenario names must be unique")
    args = data.get("presentmon_args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ConfigError("presentmon_args must be a list of strings")
    return Config(
        presentmon=data.get("presentmon"),
        scenarios=parsed,
        out_dir=str(data.get("out_dir", "out")),
        presentmon_args=list(args),
    )


def load_config(path: str | Path) -> Config:
    """Read and validate a JSON config file."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConfigError(f"{path} is not valid JSON: {error}") from error
    return parse_config(data)
