# uppercut-wave-gate

Runs one prompt over many pieces and counts only the output that passes a gate. The model is a function you pass in, so the package works with a local server, a hosted API or a command line tool. A run keeps every attempt in a ledger, can be stopped and started again without redoing accepted work, and refuses to reuse old results after the prompt, the gate, the workers or a piece have changed. It also refuses to start at all unless the gate rejects a sample you know is bad: a gate that passes everything proves nothing about the real output. Standard library only.

```sh
pip install uppercut-wave-gate
```

```python
from uppercut_wave_gate import Call, WaveConfig, Worker, run_wave

def runner(call: Call) -> str:
    # Ask your model. call.worker, call.text (the piece), call.system (the prompt file),
    # call.timeout and call.settings are what you need. Raise HostError if the host is down.
    return my_client.chat(model=call.worker.model, system=call.system, user=call.text, timeout=call.timeout)

cfg = WaveConfig(
    pieces="pieces.jsonl",           # one {"id": "p01", "text": "..."} per line
    prompt="prompt.txt",             # the system prompt
    out="run",                       # results/, failed/, attempts/, ledger.jsonl, manifest.json, summary.json
    workers=[Worker("a", "model-x")] * 2,
    gate='python check.py {out}',    # exit 0 passes
    bad="known_bad.txt",             # the gate must reject this, or the wave will not start
    retries=2,
    stop_after=8,                    # stop if 8 pieces in a row fail the gate
)
exit_code = run_wave(cfg, runner)
```

The command line does the same with a command that calls your model. The piece text arrives on standard input and the output is printed on standard output:

```sh
uppercut-wave-gate --pieces pieces.jsonl --prompt prompt.txt --out run \
    --worker a=model-x*2 --gate "python check.py {out}" --bad known_bad.txt \
    --runner-cmd "python ask.py --host {host} --model {model} --system-file {prompt_file}"
```

Run the same command again to resume: passed pieces and pieces already given up are skipped. `--retry-failed` tries the failed ones again after a prompt fix.

## Gates

A gate is a shell command string, an argument list, or a function. The only passing result is a clean pass.

| Gate | How it is called | Passes when |
| --- | --- | --- |
| `"python check.py {out}"` | Through the shell. `{out}` becomes the quoted path of the output file, `{id}` the file's name without its extension | exit code 0 |
| `["python", "check.py", "{out}"]` | Without a shell. The same two replacements in each item | exit code 0 |
| `def gate(text): ...` | With the output text | returns `True`, or `(True, "why")` |

A nonzero exit, a timeout, an exception, a command that cannot start and empty output all fail. Name the `bad` file after a real piece (`bad/p01.out`) when the gate looks the piece up by `{id}`. The probe also refuses a gate that could not give a verdict on the bad sample (it timed out or crashed), because that is not a rejection, and a `bad` file that does not exist.

## What a run writes

| File | Holds |
| --- | --- |
| `results/<id>.out` | Output that passed the gate |
| `failed/<id>.out`, `failed/<id>.why` | The last output and the reason, after `retries` more gate failures or five host errors |
| `attempts/` | The output being gated now |
| `ledger.jsonl` | One line per attempt: piece, host, model, status (`pass`, `gate_fail`, `host_error`), seconds, reason, hashes of the piece and the output |
| `manifest.json` | Hashes of the prompt, the gate (its command line and the files it names), the workers, the settings and every piece |
| `summary.json` | `pieces`, `passed`, `failed`, `remaining`, `pass_rate`, `unique_outputs`, `duplicate_count`, `diversity_ratio`, `seconds` |

A rerun with a changed prompt, gate, worker list, `settings` or piece text stops with an error and exit code 2 instead of mixing old and new results. `--invalidate-changed` clears the old results and runs again; `--new-run` insists on an empty folder. A folder with results but no manifest is refused the same way.

## Exit codes

`0` every piece run passed (look at `remaining` after a `--limit` pilot), `2` usage error, refused resume or a gate probe that failed, `3` finished with failures, `4` no workers left, `5` `--budget` reached (run again to continue), `6` `--stop-after` tripped (fix the prompt).

## API

| Name | What it does |
| --- | --- |
| `run_wave(cfg, runner)` | Run the wave; returns the exit code. Raises `WaveError` when it cannot start |
| `WaveConfig(pieces, prompt, out, workers, gate, bad, timeout=120, gate_timeout=10, retries=2, budget=None, stop_after=0, limit=None, retry_failed=False, invalidate_changed=False, new_run=False, settings={})` | Everything a run needs. `settings` is passed to the runner in `call.settings` and is part of the manifest |
| `Worker(host, model)`, `parse_workers(["a=m*2"])` | One worker slot, and the `HOST=MODEL*SLOTS` shorthand. Both names are opaque to the package |
| `Call(worker, piece_id, text, system, timeout, settings)` | What a runner receives |
| `HostError` | Raise it from a runner when the host failed. Three host errors in a row drop that worker; five fail the piece. Any other exception counts the same way |
| `command_runner(build, *, stdin=True, env=None)` | A runner that runs the argument list `build(call)` returns, with the piece on standard input, and raises `HostError` on a nonzero exit or a timeout |
| `run_gate(gate, path, timeout=10)` | `(passed, why)` for one file |
| `gate_fingerprint(gate)` | The hash the manifest keeps for the gate |
| `load_pieces(path)` | Read and check a pieces file (filename-safe unique ids, non-empty text) |
| `add_wave_arguments(parser, **defaults)`, `config_from_args(args, settings)`, `main(argv, runner)` | The shared command line options, so a project's own wave command adds only its model options |
| `summarize_samples(samples)` | Pass rate, accepted outputs per second, mean first-token time and speed, and speed multiplied by pass rate, for a benchmark of several samples |
| `output_diversity(texts)` | Unique outputs, duplicates and the diversity ratio of accepted outputs |

## Notes

- Everything model specific stays with the caller: which hosts exist, which models run, how a request is sent, and the prompt. The package only hands the runner a `Call`.
- Gate commands run through the shell with the timeout you give. A gate that starts its own children can leave them running after a timeout; give such a gate its own timeout handling.
- The runner is called from one thread per worker slot, so it must be safe to call from several threads.
- The ledger hashes pieces and outputs (16 hex digits of SHA-256) instead of storing text; the full output of a failed piece is kept in `failed/`.

## License

MIT
