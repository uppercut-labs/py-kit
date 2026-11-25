"""The wave: one prompt over many pieces, gated, retried, resumable."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, NamedTuple, Optional, Sequence

from ._files import WaveError, load_pieces, write_atomic
from ._gate import Gate, _check, gate_fingerprint, run_gate
from ._metrics import output_diversity

HOST_STRIKES = 3  # a worker quits after this many host errors in a row
PIECE_HOST_ERRORS = 5  # above HOST_STRIKES, so a dead host alone cannot fail a piece a live one could finish
_SUBDIRS = ("results", "failed", "attempts")
_WORKER = re.compile(r"^([A-Za-z0-9_.-]+)=(.+?)(?:\*(\d+))?$")


class Worker(NamedTuple):
    """One worker slot: an opaque `host` name and a `model` name, both only meaningful to the runner."""

    host: str
    model: str


@dataclass(frozen=True)
class Call:
    """What a runner receives for one attempt."""

    worker: Worker
    piece_id: str
    text: str  # the piece's text; the user message
    system: str  # the prompt file's text
    timeout: float
    settings: Mapping[str, Any]


class HostError(Exception):
    """Raise from a runner when the host failed (unreachable, nonzero exit, timeout). The message is logged.

    Any other exception from a runner counts the same way, with the exception's type in front of the message.
    """


Runner = Callable[[Call], str]


def parse_workers(specs: Sequence[str]) -> List[Worker]:
    """Expand `HOST=MODEL` and `HOST=MODEL*SLOTS` into one Worker per slot, in order."""
    workers: List[Worker] = []
    for spec in specs:
        m = _WORKER.match(spec)
        if not m:
            raise WaveError("bad --worker %s (want HOST=MODEL or HOST=MODEL*SLOTS)" % spec)
        workers += [Worker(m.group(1), m.group(2))] * int(m.group(3) or 1)
    return workers


@dataclass
class WaveConfig:
    pieces: str
    prompt: str
    out: str
    workers: Sequence[Worker]
    gate: Gate
    bad: str  # a known-bad output file the gate must reject
    timeout: float = 120  # seconds per runner call
    gate_timeout: float = 10
    retries: int = 2  # gate failures allowed per piece before it is given up
    budget: Optional[int] = None  # stop starting pieces after this many seconds
    stop_after: int = 0  # halt after this many gate failures in a row; 0 is off
    limit: Optional[int] = None  # run only the first N unfinished pieces
    retry_failed: bool = False
    invalidate_changed: bool = False
    new_run: bool = False
    settings: Dict[str, Any] = field(default_factory=dict)  # handed to the runner; part of the manifest


class Wave:
    def __init__(self, cfg: WaveConfig, runner: Runner, system: str, todo: Sequence[Dict[str, Any]]):
        self.cfg, self.runner, self.system = cfg, runner, system
        self.q: "queue.Queue[Any]" = queue.Queue()
        for p in todo:
            self.q.put((p, 0, 0))
        self.lock = threading.Lock()
        self.halt = threading.Event()
        self.tripped = False
        self.gate_fails_in_a_row = 0
        self.deadline = time.time() + cfg.budget if cfg.budget else None
        self.prompt_hash = hashlib.sha256(system.encode()).hexdigest()[:12]
        self.ledger = open(os.path.join(cfg.out, "ledger.jsonl"), "a", encoding="utf-8")

    def log(self, **rec: Any) -> None:
        with self.lock:
            self.ledger.write(json.dumps({"t": round(time.time()), "prompt": self.prompt_hash, **rec}) + "\n")
            self.ledger.flush()

    def close(self) -> None:
        with self.lock:
            if not self.ledger.closed:
                self.ledger.close()

    def call(self, host: str, model: str, piece: Mapping[str, Any]) -> str:
        return self.runner(Call(Worker(host, model), piece["id"], piece["text"], self.system,
                                self.cfg.timeout, self.cfg.settings))

    def give_up(self, pid: str, text: str, why: str) -> None:
        write_atomic(os.path.join(self.cfg.out, "failed", pid + ".why"), why + "\n")
        write_atomic(os.path.join(self.cfg.out, "failed", pid + ".out"), text or "")

    def work(self, host: str, model: str) -> None:
        strikes = 0
        while not self.halt.is_set():
            if self.deadline and time.time() > self.deadline:
                return
            try:
                piece, tries, host_errors = self.q.get_nowait()
            except queue.Empty:
                return
            pid, t0 = piece["id"], time.time()
            err: Optional[str] = None
            try:
                text = str(self.call(host, model, piece) or "").strip()
            except HostError as exc:
                text, err = "", str(exc)[-300:] or "host error"
            except Exception as exc:
                text, err = "", "%s: %s" % (type(exc).__name__, str(exc)[-200:])
            secs = round(time.time() - t0, 1)
            piece_hash = hashlib.sha256(piece["text"].encode("utf-8")).hexdigest()[:16]
            if err is not None:
                strikes += 1
                self.log(id=pid, host=host, model=model, status="host_error", secs=secs, why=err, piece_hash=piece_hash)
                if host_errors + 1 >= PIECE_HOST_ERRORS:
                    self.give_up(pid, text, "host error: " + err)
                else:
                    self.q.put((piece, tries, host_errors + 1))
                if strikes >= HOST_STRIKES:
                    sys.stderr.write("wave: dropping %s after %d host errors: %s\n" % (host, strikes, err))
                    return
                continue
            strikes = 0
            path = os.path.join(self.cfg.out, "attempts", pid + ".out")
            write_atomic(path, text)
            if not text:
                passed, why = False, "empty output"
            else:
                passed, why = run_gate(self.cfg.gate, path, self.cfg.gate_timeout)
            out_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else None
            self.log(id=pid, host=host, model=model, status="pass" if passed else "gate_fail",
                     secs=secs, why=why, piece_hash=piece_hash, output_hash=out_hash)
            with self.lock:
                self.gate_fails_in_a_row = 0 if passed else self.gate_fails_in_a_row + 1
                if self.cfg.stop_after and self.gate_fails_in_a_row >= self.cfg.stop_after:
                    self.tripped = True
                    self.halt.set()
            if passed:
                os.replace(path, os.path.join(self.cfg.out, "results", pid + ".out"))
            elif tries + 1 > self.cfg.retries:
                self.give_up(pid, text, why)
                if os.path.exists(path):
                    os.remove(path)
            else:
                self.q.put((piece, tries + 1, host_errors))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clear(folder: str) -> None:
    if not os.path.isdir(folder):
        return
    for name in os.listdir(folder):
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass


def _check_resume(cfg: WaveConfig, manifest_path: str, prompt_hash: str, gate_hash: str,
                  workers: List[List[str]], settings: Dict[str, Any], pieces_map: Dict[str, str]) -> None:
    """Refuse to reuse outputs that were accepted under a different prompt, gate, workers, settings or piece."""
    out = cfg.out
    if os.path.exists(manifest_path):
        if cfg.new_run:
            raise WaveError("--new-run specified, but output directory '%s' already contains an existing run manifest" % out)
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                old = json.load(fh)
        except Exception as exc:
            raise WaveError("corrupt manifest.json in %s: %s" % (out, exc))
        changed = [name for name, differs in (
            ("prompt", old.get("prompt_hash") != prompt_hash),
            ("gate command/file", old.get("gate_hash") != gate_hash),
            ("worker models", old.get("workers") != workers),
            ("generation settings", old.get("generation_settings") != settings),
        ) if differs]
        if changed:
            if not cfg.invalidate_changed:
                raise WaveError(
                    "run manifest mismatch: %s changed since previous run in %s.\n"
                    "Silent reuse of stale accepted outputs is refused.\n"
                    "Pass --invalidate-changed to re-evaluate, or specify a new --out directory." % (", ".join(changed), out))
            sys.stderr.write("wave: configuration changed; invalidating all prior outputs (--invalidate-changed)\n")
            for sub in _SUBDIRS:
                _clear(os.path.join(out, sub))
        old_pieces = old.get("pieces", {})
        for pid, digest in pieces_map.items():
            if pid in old_pieces and old_pieces[pid] != digest:
                if not cfg.invalidate_changed:
                    raise WaveError("piece '%s' content changed since previous run in %s.\n"
                                    "Pass --invalidate-changed to re-run changed pieces." % (pid, out))
                for ext in (".out", ".why"):
                    for sub in ("results", "failed"):
                        path = os.path.join(out, sub, pid + ext)
                        if os.path.exists(path):
                            os.remove(path)
        return
    # No manifest: outputs already here were never certified by this run's prompt and gate.
    results_dir, failed_dir = os.path.join(out, "results"), os.path.join(out, "failed")
    has_results = os.path.isdir(results_dir) and any(f.endswith(".out") for f in os.listdir(results_dir))
    has_failed = os.path.isdir(failed_dir) and any(f.endswith((".out", ".why")) for f in os.listdir(failed_dir))
    if has_results or has_failed:
        if not cfg.invalidate_changed:
            raise WaveError("untrusted outputs found in '%s' without a valid manifest.json.\n"
                            "Silent certification of manifestless outputs is refused.\n"
                            "Pass --invalidate-changed to re-evaluate, or specify a clean --out directory." % out)
        for sub in _SUBDIRS:
            _clear(os.path.join(out, sub))


def _gate_label(gate: Gate) -> Any:
    if isinstance(gate, str):
        return gate
    if callable(gate):
        return getattr(gate, "__qualname__", "callable")
    return list(gate)


def run_wave(cfg: WaveConfig, runner: Runner) -> int:
    """Run the wave and return its exit code. Raises WaveError when it cannot start (exit code 2 on a command line).

    0 every piece run passed (check `remaining` in summary.json after a `limit` pilot); 3 finished with
    failures; 4 no workers left; 5 the `budget` ran out (run again to continue); 6 `stop_after` tripped
    (fix the prompt).
    """
    if not cfg.workers:
        raise WaveError("no workers")
    with open(cfg.prompt, encoding="utf-8") as fh:
        system = fh.read().strip()
    if not system:
        raise WaveError("empty prompt file")
    pieces = load_pieces(cfg.pieces)
    out = cfg.out
    for sub in _SUBDIRS:
        os.makedirs(os.path.join(out, sub), exist_ok=True)

    manifest_path = os.path.join(out, "manifest.json")
    prompt_hash = _sha(system)
    gate_hash = gate_fingerprint(cfg.gate)
    pieces_map = {p["id"]: _sha(p["text"]) for p in pieces}
    workers = [[w[0], w[1]] for w in cfg.workers]
    settings = dict(cfg.settings)
    _check_resume(cfg, manifest_path, prompt_hash, gate_hash, workers, settings, pieces_map)

    # The gate has to reject a sample that is known to be bad, or it proves nothing about the real output.
    if not os.path.isfile(cfg.bad):
        raise WaveError("the known-bad sample %s does not exist, so the gate cannot be tested" % cfg.bad)
    accepted, why, errored = _check(cfg.gate, cfg.bad, cfg.gate_timeout)
    if accepted:
        raise WaveError("the gate passed the known-bad output %s; it proves nothing, so the wave won't start" % cfg.bad)
    if errored:
        raise WaveError("the gate could not judge the known-bad output %s (%s); it proves nothing, so the wave won't start"
                        % (cfg.bad, why))

    write_atomic(manifest_path, json.dumps({
        "version": "1.0",
        "prompt_hash": prompt_hash,
        "gate_hash": gate_hash,
        "gate_template": _gate_label(cfg.gate),
        "workers": workers,
        "generation_settings": settings,
        "pieces": pieces_map,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=2) + "\n")

    def done(sub: str, piece: Mapping[str, Any]) -> bool:
        return os.path.exists(os.path.join(out, sub, piece["id"] + ".out"))

    def failed_done(piece: Mapping[str, Any]) -> bool:
        return done("failed", piece) or os.path.exists(os.path.join(out, "failed", piece["id"] + ".why"))

    todo = [p for p in pieces if not done("results", p) and (cfg.retry_failed or not failed_done(p))]
    if cfg.retry_failed:
        for p in todo:
            for ext in (".out", ".why"):
                path = os.path.join(out, "failed", p["id"] + ext)
                if os.path.exists(path):
                    os.remove(path)
    if cfg.limit:
        todo = todo[:cfg.limit]

    wave = Wave(cfg, runner, system, todo)
    t0 = time.time()
    try:
        threads = [threading.Thread(target=wave.work, args=tuple(w)) for w in cfg.workers]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        wave.close()

    passed = sum(done("results", p) for p in pieces)
    failed = sum(failed_done(p) for p in pieces)
    finished = passed + failed
    accepted_texts = []
    results_dir = os.path.join(out, "results")
    for name in sorted(os.listdir(results_dir)):
        if name.endswith(".out"):
            try:
                with open(os.path.join(results_dir, name), encoding="utf-8", errors="replace") as fh:
                    accepted_texts.append(fh.read())
            except OSError:
                pass
    diversity = output_diversity(accepted_texts)
    summary = {
        "pieces": len(pieces),
        "passed": passed,
        "failed": failed,
        "remaining": len(pieces) - passed - failed,
        "pass_rate": round(passed / finished, 4) if finished else (1.0 if passed else 0.0),
        "unique_outputs": diversity["unique_outputs"],
        "duplicate_count": diversity["duplicate_count"],
        "diversity_ratio": diversity["diversity_ratio"],
        "prompt": wave.prompt_hash,
        "manifest": prompt_hash[:16],
        "seconds": round(time.time() - t0),
    }
    write_atomic(os.path.join(out, "summary.json"), json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))
    if wave.tripped:
        return 6
    if not wave.q.empty():
        return 5 if wave.deadline and time.time() > wave.deadline else 4
    return 3 if failed else 0
