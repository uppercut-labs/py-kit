"""Run scenarios against a desktop app, record frame timing with PresentMon, summarize to JSON."""
from .config import Config, ConfigError, Scenario, load_config, parse_config
from .parse import (
    FrameParseError,
    parse_marks,
    parse_presentmon_csv,
    percentile,
    summarize_displayed,
    summarize_frames,
    summarize_runs,
    summarize_startup_marks,
)
from .runner import (
    build_presentmon_command,
    capture_frames,
    find_capture_csv,
    find_descendant_pid,
    list_processes,
    resolve_presentmon,
    run_scenario,
)

__all__ = [
    "Config",
    "ConfigError",
    "FrameParseError",
    "Scenario",
    "build_presentmon_command",
    "capture_frames",
    "find_capture_csv",
    "find_descendant_pid",
    "list_processes",
    "load_config",
    "parse_config",
    "parse_marks",
    "parse_presentmon_csv",
    "percentile",
    "resolve_presentmon",
    "run_scenario",
    "summarize_displayed",
    "summarize_frames",
    "summarize_runs",
    "summarize_startup_marks",
]
__version__ = "1.0.0"
