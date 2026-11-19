# uppercut-subprocess-runner

Runs a command with a timeout, an output cap and cancellation, and returns a result object with the exit code, the captured output, the last bytes of each stream and how the run ended. When it has to stop the command it kills the whole process tree: the process group on macOS and Linux, a process-group break followed by `taskkill /F /T` on Windows. It sends a soft signal first, waits a grace period, then forces the kill. Standard library only.

```sh
pip install uppercut-subprocess-runner
```

```python
import threading
from uppercut_subprocess_runner import run

cancel = threading.Event()  # call cancel.set() from another thread to stop the run

result = run(
    ["python", "build.py", "--fast"],
    cwd="project",
    timeout=120,
    max_output=2_000_000,
    grace=5,
    cancel=cancel,
)

if result.timed_out:
    print("gave up after", round(result.duration), "seconds")
elif not result.ok:
    print(result.error_tail())
```

Use `check=True` to raise `CommandError` instead, with the tail of the output in the message and the full result in `error.result`.

## Functions

| Function | Returns |
| --- | --- |
| `run(argv, **options)` | `RunResult` |
| `kill_tree(process_or_pid, grace=5.0)` | `KillInfo` |
| `popen_group(argv, **popen_options)` | a `subprocess.Popen` started in its own session or process group |
| `popen_group_kwargs(no_window=False)` | the `Popen` keyword arguments that `popen_group` adds |

## Options for `run`

| Option | Default | Meaning |
| --- | --- | --- |
| `cwd`, `env` | inherited | Working directory and environment of the command |
| `input` | none | Bytes written to stdin. Without it stdin is closed |
| `timeout` | none | Seconds before the run is stopped with `stop_reason == "timeout"` |
| `max_output` | `10_000_000` | Bytes kept from the start of each stream. `None` keeps everything |
| `stop_on_output_limit` | `True` | Stop the command when a stream passes `max_output`. `False` keeps reading and drops the excess |
| `tail_bytes` | `8192` | Bytes kept from the end of each stream, even past `max_output` |
| `grace` | `5.0` | Seconds between the soft signal and the forced kill |
| `cancel` | none | A `threading.Event`. Setting it stops the run with `stop_reason == "cancelled"` |
| `on_output` | none | Called as `on_output("stdout" or "stderr", chunk)` from reader threads. Exceptions it raises are ignored |
| `kill_process_tree` | `True` | `False` signals only the command itself and leaves its descendants running |
| `drain_timeout` | `1.0` | Seconds to wait for the pipes to close after the command exits. If a descendant still holds them, the tree is killed |
| `no_window` | `False` | Windows only: start the command without a console window |
| `check` | `False` | Raise `CommandError` unless the command exits with code 0 |

## RunResult

| Field | Meaning |
| --- | --- |
| `argv` | The command that ran |
| `exit_code` | Exit code. `None` if it never started. Negative signal number after a kill on macOS and Linux |
| `stop_reason` | `"exited"`, `"timeout"`, `"cancelled"`, `"output-limit"` or `"spawn-failed"` |
| `timed_out`, `cancelled`, `ok` | Shortcuts. `ok` means it exited by itself with code 0 |
| `truncated` | A stream produced more than `max_output` bytes |
| `duration` | Seconds from start to finish |
| `stdout`, `stderr` | Captured bytes, at most `max_output` each. `stdout_text` and `stderr_text` decode them |
| `stdout_tail`, `stderr_tail` | The last `tail_bytes` of each stream. `error_tail(limit)` returns the stderr tail as text, or the stdout tail if stderr is empty |
| `pid` | Process id of the command |
| `kill` | A `KillInfo` when the run was stopped, otherwise `None` |
| `error` | The operating system's message when `stop_reason == "spawn-failed"` |

`KillInfo` has `method`, `already_exited`, `graceful` (the soft signal was enough) and `forced` (SIGKILL or `taskkill /F` was needed).

## Notes

- `kill_tree` on macOS and Linux needs the target to lead its own process group. Processes started by `run` and `popen_group` do. It raises `ValueError` rather than signal the calling process's own group.
- On Windows the soft step is `CTRL_BREAK_EVENT`, which ends most console programs. Descendants of a command that exits during the grace period cannot be found afterwards and keep running.
- Error messages from `check=True` name the program but not its arguments.

## License

MIT
