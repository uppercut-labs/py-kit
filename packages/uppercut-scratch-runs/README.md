# uppercut-scratch-runs

Disposable run directories for builds, checks and other jobs that write a lot of throwaway files. `new` makes `<root>/run-<32 hex digits>` with an ownership marker inside, `clean` removes exactly that directory and nothing it links to, and `run -- <command>` does both around one command. The roots, the marker name and the exported variable come from the project's own configuration, so several projects can share a machine without touching each other's runs.

```sh
pip install uppercut-scratch-runs
```

Requires Python 3.10 or newer. On 3.10 it installs `tomli` to read the configuration; later versions use `tomllib`.

## Configuration

Add a table to the project's `pyproject.toml`:

```toml
[tool.uppercut-scratch-runs]
project = "my-game"          # the marker defaults to .my-game-run
variable = "MY_GAME_SCRATCH" # run sets this as well as TMPDIR, TEMP and TMP

[tool.uppercut-scratch-runs.roots]
windows = ["C:/scratch/my-game", "D:/bulk/my-game"]
wsl = ["/scratch/my-game"]
default = ["~/scratch/my-game"]
```

| Key | Meaning |
| --- | --- |
| `project` | Optional name: letters, digits, `.`, `_` and `-`. Used for the default marker. |
| `marker` | The ownership marker's file name. Defaults to `.<project>-run`, or `.scratch-run` without a project. |
| `variable` | The extra environment variable `run` exports. Defaults to `SCRATCH_RUN`. |
| `roots.<platform>` | One path or a list of paths. The first is the normal root; `--large` picks the last. |

The platform keys are `windows`, `wsl`, `linux`, `macos` and `default`. The first key that is set wins, in this order: `windows` then `default` on Windows; `wsl`, `linux`, then `default` under WSL; `linux` then `default` on other Linux; `macos` then `default` on macOS. A leading `~` is expanded. Every root must be absolute, and each key is checked with the path rules of the platform it is for, so one file can list Windows and POSIX roots.

Without `--config`, the command reads the nearest `pyproject.toml` at or above the working directory that has the table. Scripts that run from anywhere should pass `--config` explicitly.

## Command line

```sh
uppercut-scratch-runs new [--large]                 # print the new run's absolute path
uppercut-scratch-runs run [--large] -- make check   # run a command in a new run, then remove it
uppercut-scratch-runs clean /path/to/run-...        # remove one run made by new
```

`python -m uppercut_scratch_runs` is the same command. Every subcommand takes these options, which override the file:

| Option | Meaning |
| --- | --- |
| `--config FILE` | TOML file with a `[tool.uppercut-scratch-runs]` table. |
| `--project NAME`, `--marker NAME`, `--variable NAME` | As in the table. |
| `--root DIR` | Use this root on every platform instead of the configured ones. Repeat it for more; the last is the large one. |

`run` keeps the working directory, sets `TMPDIR`, `TEMP`, `TMP` and the configured variable to the run's path, prints `Scratch: <path>` on standard error, and removes the run when the command returns, including a nonzero exit. It exits with the command's status; a command killed by a signal gives 128 plus the signal number, as a shell would. Errors print one line and exit 1.

A shell script that needs a run for its own lifetime:

```sh
stage=$(uppercut-scratch-runs new --config "$root/pyproject.toml")
trap 'uppercut-scratch-runs clean --config "$root/pyproject.toml" "$stage"' EXIT
```

`clean` must be given the same configuration as `new`, because it only accepts a run directly under one of that platform's roots, with that marker.

## Python

```python
from uppercut_scratch_runs import ScratchConfig, load_config

config = load_config("pyproject.toml")          # or ScratchConfig(roots={"default": (Path("/scratch/x"),)})

with config.session() as run:                   # removed afterwards, also on an exception
    build(output=run / "out")

result = config.run(["make", "check"], large=True, capture_output=True)

run = config.new()
config.clean(run)
```

| Name | What it does |
| --- | --- |
| `load_config(path=None, *, start=None)` | Settings from a TOML file, or from the nearest `pyproject.toml` with the table. With no file found, the defaults with no roots. |
| `ScratchConfig(roots, marker, variable, project)` | The settings. `ScratchConfig.from_mapping(table)` builds them from a table. |
| `.platform_roots()` / `.root(large=False)` | The roots for this machine, and the one a new run goes under. |
| `.new(large=False)` / `.clean(run)` | Allocate a run, or remove one. |
| `.session(large=False)` | Context manager around `new` and `clean`. |
| `.run(command, large=False, **kwargs)` | `subprocess.run` with the run exported, then `clean`. Returns the `CompletedProcess`. |
| `.environment(run, base=None)` | The environment `run` uses. |
| `create(root, *, marker)` / `clean(run, roots, *, marker)` | The two operations without settings. |
| `check_root(root)`, `is_link(path)`, `platform_keys()`, `find_config(start=None)` | The helpers they use. |

Settings errors raise `ConfigError`, a `ValueError`. A refused cleanup raises `ValueError`; a file the system will not delete raises `OSError`.

## What cleanup checks

Before removing anything, `clean` checks that:

- the path is exactly `<root>/run-<32 lowercase hex digits>` for one of the platform's roots;
- no directory from the filesystem root down to the run's root is a symbolic link or, on Windows, a reparse point such as a junction;
- the run itself is not a link and resolves to its own path;
- the marker is a regular file, not a link, and holds the run's own name;
- nothing is mounted at or below the run. On Linux this reads `/proc/self/mountinfo`, which also finds bind mounts on the same device; on other POSIX systems each directory is checked with `is_mount`.

Then it removes the run's contents. Symbolic links and Windows junctions are removed as links, so their targets are never entered, at the top of the run and inside it. The marker goes last: if a file is locked, as Windows does for files another process has open, the cleanup stops with the marker still in place and can be run again once the file is free. Cleaning a run that is already gone succeeds, so a retried cleanup is safe.

New runs are created with mode 0700 on POSIX. Roots are created as needed, after the same link check on their parents.

## License

MIT
