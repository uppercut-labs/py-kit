# uppercut-tts-worker

A text-to-speech model is slow to load and takes the whole GPU, so narration tools start it once as a worker process and send it one sentence at a time. This package is the shared part: the line protocol between the tool and the worker, a lock file that lets only one GPU job run on a machine and queues the others, a job runner that resumes where it stopped, and the worker-side loop for writing a worker. No voice, voice sample, model name or weights are in it: those belong to whoever runs the worker. Standard library only.

```sh
pip install uppercut-tts-worker
```

## The protocol

One JSON object per line in each direction, over the worker's standard input and output.

```text
worker  -> {"ready": true, "device": "cuda:0", "dtype": "float32"}                    once, when the model is loaded
tool    -> {"text": "...", "output_path": "a.wav", "seed": 12, "settings": {...}, "voice_id": "v1"}
worker  -> {"ok": true, "sample_rate": 24000, "samples": 48000, "wall_s": 1.2}
worker  -> {"ok": false, "error": "RuntimeError: ..."}                                 for a sentence it could not say
```

`voice_id` is sent only when there is one. Lines that are not JSON objects, such as a library's log output, are skipped by the reader.

## Write a worker

```python
from uppercut_tts_worker import serve

def synthesize(request):
    audio = my_model.say(request["text"], seed=request["seed"], **request["settings"])
    audio.write_wav(request["output_path"])
    return {"sample_rate": audio.rate, "samples": len(audio)}

serve(synthesize, ready={"device": "cuda:0", "dtype": "float32"})
```

An exception becomes an `ok: false` response and the loop goes on with the next sentence.

## Use a worker

```python
from uppercut_tts_worker import GpuLock, WorkerProcess, make_request, wav_seconds

with GpuLock("runs/.gpu.lock", wait_s=2700, on_wait=print):          # queue behind another GPU job
    with WorkerProcess("python", "worker.py", ["--ckpt-dir", "weights"], log_path="worker.log",
                       env={"HF_HUB_OFFLINE": "1"}) as worker:           # starts it and waits for the ready line
        reply = worker.request(make_request("Hello there.", "hello.wav", seed=7, settings={"language": "English"}))
        if reply["ok"]:
            print(wav_seconds("hello.wav"), "seconds")
```

## Run a job file

`uppercut-tts-worker job.json` (or `python -m uppercut_tts_worker job.json`, or `run_job(job)`) generates a list of sentences in one go, on the machine that has the GPU:

```json
{"worker": {"python": "python", "script": "worker.py", "args": ["--ckpt-dir", "weights"], "env": {"HF_HUB_OFFLINE": "1"}},
 "lock": "runs/.gpu.lock", "lock_wait_s": 2700,
 "out_dir": "tts/job1",
 "items": [{"id": "s001", "text": "First sentence.", "seed": 123, "settings": {"language": "English"}, "voice_id": "v1"}]}
```

Each item is written to `out_dir/<id>.wav` and recorded in `out_dir/results.json` with the SHA-256 of its text and its seed. An item whose WAV exists and whose text and seed still match is skipped, so an interrupted job resumes and an edited sentence is generated again. The lock is taken only when something is left to do. The exit code is 1 if any item failed or the job could not run.

## API

| Name | What it does |
| --- | --- |
| `make_request(text, output_path, *, seed=None, settings=None, voice_id=None)` | A request object |
| `read_message(stream)`, `write_message(stream, message)` | One protocol line in or out. `read_message` skips lines that are not JSON objects and returns None at the end |
| `serve(synthesize, *, ready=None, stdin=None, stdout=None)` | The worker loop |
| `WorkerProcess(python, script, args=(), *, env=None, log_path=None, cwd=None, raise_priority=False)` | Start a worker (`start()`, `wait_ready()`, `request(r)`, `close()`, or use it as a context manager). Raises `WorkerError` when it does not become ready or exits mid-request. A reply is waited for without a time limit |
| `GpuLock(path, *, wait_s=0, poll_s=5, on_wait=None)` | The lock file. Context manager, or `acquire()` and `release()`. Raises `LockBusy` |
| `process_alive(pid)` | Whether a process is running, on Windows and elsewhere |
| `wav_seconds(path)` | Length of a WAV of any sample format, float files included |
| `raise_tree_priority(pid)` | Windows only: raise a process and its children to above normal priority, which stops ssh-started processes from running at low clocks. Returns the ids it changed |
| `run_job(job, *, log, lock_log)`, `main(argv)` | The job runner and its command |

## Notes

- The lock holds the owner's process id. A lock whose owner has exited is removed and taken; a lock held by a live process is waited for and never deleted. Two machines sharing a network folder do not share a lock: process ids mean nothing across machines.
- `release()` removes the file only while it still names this process, so a lock that was taken over is left alone.
- The fake worker in the tests (`tests/fake_worker.py`) is a complete example of a worker built on `serve`.
- Tested at 1.0.0: the tests pass on macOS 27.0 (Apple M6, Python 3.13.18; 28 passed, 1 skipped) and on Windows 11 Pro (10.0.26300, Python 3.14.0; 29 passed). The Windows run covers `process_alive` and `raise_tree_priority` against a live child (`time.sleep(30)`) the test starts and kills: the child is alive, `raise_tree_priority` returns its id, `GetPriorityClass` reads ABOVE_NORMAL, and `process_alive` is false after the kill. That run found that `GpuLock.release()` removed the lock file while it still had it open, which Windows refuses, so the lock stayed behind. The file is now closed first.

## License

MIT
