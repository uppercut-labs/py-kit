# uppercut-presentmon-measure

Runs startup, idle and workload scenarios against a desktop app on Windows, records frame timing with Intel PresentMon, and writes a JSON summary: frame-time percentiles, average fps, dropped frames, frames that missed the display refresh, and startup marks the app reports. Parsing a PresentMon CSV and summarizing it are plain functions that work on any platform. Launching an app and capturing needs Windows and a PresentMon executable.

## Install

```
pip install uppercut-presentmon-measure
```

Python 3.10 or newer, no runtime dependencies. You supply PresentMon (the console or the service CLI); pass its path with `--presentmon`, the `presentmon` config key, or the `PRESENTMON_CLI` environment variable. If none is set, `PresentMonCli` and `PresentMon` are looked up on `PATH`.

## Example

`measure.json`:

```json
{
  "presentmon": "C:/tools/PresentMonCli.exe",
  "out_dir": "out",
  "scenarios": [
    {
      "name": "startup",
      "kind": "startup",
      "command": ["my-app.exe"],
      "runs": 5,
      "marks_file": "out/marks.json"
    },
    {
      "name": "idle",
      "kind": "idle",
      "command": ["my-app.exe"],
      "warmup_seconds": 10,
      "capture_seconds": 15,
      "refresh_hz": 60
    },
    {
      "name": "scroll",
      "kind": "workload",
      "command": ["my-app.exe"],
      "warmup_seconds": 10,
      "capture_seconds": 10,
      "workload_command": ["node", "drive-scroll.js"],
      "capture_process_name": "my-app.exe",
      "refresh_hz": 60
    }
  ]
}
```

```
uppercut-presentmon-measure run --config measure.json
```

The summary is printed and written to `out/result.json`; each capture's raw CSV is kept as `out/<name>.csv`. A single scenario can also come from arguments:

```
uppercut-presentmon-measure run --kind idle --capture 15 --presentmon PresentMonCli.exe -- my-app.exe
```

To summarize a CSV you already have, on any platform:

```
uppercut-presentmon-measure summarize frames.csv --refresh-hz 60
```

## Scenarios

| Kind | What happens |
| --- | --- |
| `startup` | Launches the app `runs` times (default 3). After each launch it waits for the app to write `marks_file`, then stops the app. Each mark is reported in milliseconds since launch, with min, median and max across runs. |
| `idle` | Launches the app once, waits `warmup_seconds`, then captures `capture_seconds` of frames with nothing running. |
| `workload` | Like `idle`, and starts `workload_command` when the capture starts. The command's exit code is reported. |

The marks file is JSON with epoch milliseconds, either `{"marks": {"ready": 1700000000123}}` or the flat form `{"ready": 1700000000123}`. Scenarios other than `startup` also report marks when `marks_file` is set.

A capture targets the launched process. Apps that present from a child process (Chromium and Electron present from their GPU process) can be captured with `capture_process_name` instead. PresentMon records nothing while a window is idle and not presenting, so an idle capture can legitimately come back with `"frames": 0` and a `note`.

## Scenario options

| Option | Default | Meaning |
| --- | --- | --- |
| `name` | the kind | Unique name; also the CSV file name. |
| `kind` | required | `startup`, `idle` or `workload`. |
| `command` | required | App command line, as a list. |
| `cwd`, `env` | none | Working directory and extra environment for the app and the workload. |
| `runs` | 3 for `startup`, else 1 | Launches for `startup`. |
| `warmup_seconds` | 5 | Wait after launch before capturing. |
| `capture_seconds` | 10 | Length of the PresentMon capture. |
| `workload_command` | none | Required for `workload`. |
| `marks_file` | none | Required for `startup`. |
| `marks_timeout_seconds` | 30 | How long `startup` waits for marks. |
| `capture_process_name` | launched pid | Capture a process by name. |
| `refresh_hz` | none | Adds `missedRefreshFrames`: frames longer than 1.5 refresh intervals. |

Top-level config keys: `scenarios`, `presentmon`, `out_dir` (default `out`), and `presentmon_args` (extra arguments appended to every capture command).

## Python API

| Function | Description |
| --- | --- |
| `parse_presentmon_csv(text, frame_time_column=None) -> list[dict]` | Frames as `{"frame_time_ms", "dropped"}`. Reads `FrameTime` or `msBetweenPresents`. |
| `summarize_frames(frames, refresh_hz=None) -> dict` | Frame count, duration, average fps, `frameTimeMs` min, mean, p50, p90, p95, p99 and max, dropped frames and percent. |
| `percentile(values, pct) -> float` | Linear-interpolated percentile. |
| `summarize_runs(values_ms) -> dict` | Min, median and max of repeated runs; `None` counts as a failed run. |
| `summarize_startup_marks(marks, launch_epoch_ms) -> dict` | Marks as milliseconds since launch. |
| `parse_marks(text) -> dict` | Reads a marks file. |
| `load_config(path)`, `parse_config(data)` | Validate a config into `Config` and `Scenario` objects. |
| `build_presentmon_command(executable, output_file, seconds, pid=None, process_name=None, extra_args=())` | The capture command line. |
| `resolve_presentmon(option=None, env=None) -> str` | Locate the executable. |
| `run_scenario(scenario, presentmon, out_dir, extra_args=()) -> dict` | Run one scenario. Windows only. |

## Errors

| Error | Raised when |
| --- | --- |
| `ConfigError` | The config is missing a field or a value has the wrong type. |
| `FrameParseError` | A CSV has no frame-time column or a non-numeric value. |
| `FileNotFoundError` | The PresentMon executable cannot be found. |
| `RuntimeError` | A scenario is run off Windows, or the app exits before the capture starts. |

The command line exits with status 2 and prints the message for each of these.

## License

MIT
