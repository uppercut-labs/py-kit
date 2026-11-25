"""Run a job file: many sentences through one resident worker, under the GPU lock, resumable."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from .lock import GpuLock, LockBusy
from .protocol import make_request
from .worker import WorkerError, WorkerProcess

SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _save(path: str, data: Dict[str, Any]) -> None:
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


def run_job(job: Dict[str, Any], *, log: Callable[[str], None] = lambda line: print(line, flush=True),
            lock_log: Callable[[str], None] = lambda line: print(line, file=sys.stderr, flush=True)) -> int:
    """Generate every item of the job that is not already done; returns 0, or 1 if any item failed.

    The job is a dict::

        {"worker": {"python": "...", "script": "...", "args": [...], "env": {...}},
         "lock": "path/to/.gpu.lock", "lock_wait_s": 2700,
         "out_dir": "path",
         "items": [{"id": "s001", "text": "...", "seed": 123, "settings": {...}, "voice_id": "..."}]}

    Each item is written to `out_dir/<id>.wav` and recorded in `out_dir/results.json` with the SHA-256 of its
    text and its seed. An item whose WAV exists and whose recorded text hash and seed match is skipped, so an
    interrupted job resumes and an edited sentence is generated again. `settings` defaults to
    `{"language": "English"}`. The lock is taken only when something is left to generate, and the worker's
    standard error goes to `out_dir/worker.log`. Raises LockBusy when the lock stays taken for `lock_wait_s`
    (default 2700) seconds, and WorkerError when the worker cannot start or dies.
    """
    out = job["out_dir"]
    os.makedirs(out, exist_ok=True)
    items: Sequence[Dict[str, Any]] = job["items"]
    for item in items:
        if not SAFE_ID.match(str(item.get("id", ""))):
            raise ValueError("item id %r is not filename safe" % (item.get("id"),))
    results_path = os.path.join(out, "results.json")
    results: Dict[str, Any] = {}
    if os.path.exists(results_path):
        with open(results_path, encoding="utf-8") as fh:
            results = json.load(fh)
    todo = [it for it in items
            if not (os.path.exists(os.path.join(out, it["id"] + ".wav"))
                    and results.get(it["id"], {}).get("text_sha256") == _sha(it["text"])
                    and results[it["id"]].get("seed") == it.get("seed"))]
    log("%d/%d items to generate" % (len(todo), len(items)))
    if not todo:
        return 0
    spec = job["worker"]
    with GpuLock(job["lock"], wait_s=float(job.get("lock_wait_s", 2700)), on_wait=lock_log):
        with WorkerProcess(spec["python"], spec["script"], spec.get("args", ()), env=spec.get("env"),
                           log_path=os.path.join(out, "worker.log"), raise_priority=True) as worker:
            log("worker ready on %s (%s)" % (worker.ready.get("device"), worker.ready.get("dtype")))
            failed = 0
            for item in todo:
                wav = os.path.join(out, item["id"] + ".wav")
                request = make_request(item["text"], wav, seed=item.get("seed"),
                                       settings=item.get("settings") or {"language": "English"},
                                       voice_id=item.get("voice_id"))
                started = time.time()
                response = worker.request(request)
                ok = bool(response.get("ok"))
                failed += not ok
                results[item["id"]] = {"ok": ok, "text_sha256": _sha(item["text"]), "seed": item.get("seed"),
                                       "sample_rate": response.get("sample_rate"), "samples": response.get("samples"),
                                       "wall_s": response.get("wall_s"), "error": response.get("error")}
                _save(results_path, results)
                log("[%s] %s %ss  %s" % ("ok" if ok else "FAILED", item["id"], round(time.time() - started, 1),
                                         item["text"][:70]))
    return 1 if failed else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """`uppercut-tts-worker job.json`: run the job; exit 1 if an item failed or the job could not run."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] in ("-h", "--help"):
        sys.stderr.write((run_job.__doc__ or "") + "\nusage: uppercut-tts-worker job.json\n")
        return 0 if args[:1] in (["-h"], ["--help"]) else 2
    with open(args[0], encoding="utf-8") as fh:
        job = json.load(fh)
    try:
        return run_job(job)
    except (LockBusy, WorkerError) as exc:
        sys.stderr.write("%s\n" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
