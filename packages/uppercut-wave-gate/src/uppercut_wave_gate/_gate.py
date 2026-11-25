"""Run a gate over one output file. A gate fails closed: only a clean pass counts."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple, Sequence, Union

# A gate is a shell command string, an argument list, or a function from the output text to a verdict.
Verdict = Union[bool, "tuple[bool, str]"]
Gate = Union[str, Sequence[str], Callable[[str], Verdict]]


class GateResult(NamedTuple):
    passed: bool
    why: str


def _check(gate: Gate, path: str, timeout: float) -> "tuple[bool, str, bool]":
    """(passed, why, errored). `errored` is True when the gate could not give a verdict at all."""
    if callable(gate):
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
            verdict = gate(text)
        except Exception as exc:  # a gate that crashes must not pass anything
            return False, "gate error: %s" % exc, True
        if isinstance(verdict, tuple):
            ok, why = bool(verdict[0]), str(verdict[1]) if len(verdict) > 1 else ""
            return ok, why or ("ok" if ok else "gate rejected the output"), False
        return bool(verdict), "ok" if verdict else "gate rejected the output", False
    stem = os.path.splitext(os.path.basename(path))[0]
    if isinstance(gate, str):
        cmd = gate.replace("{out}", '"%s"' % path).replace("{id}", stem)
        shell = True
    else:
        cmd = [str(part).replace("{out}", path).replace("{id}", stem) for part in gate]
        shell = False
    label = cmd if isinstance(cmd, str) else " ".join(cmd)
    try:
        r = subprocess.run(cmd, shell=shell, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "gate error: command '%s' timed out after %s seconds" % (label, timeout), True
    except Exception as exc:
        return False, "gate error: %s" % exc, True
    if r.returncode == 0:
        return True, "ok", False
    msg = (r.stderr or r.stdout).strip()
    return False, "gate exit %d: %s" % (r.returncode, msg[:200] if msg else "nonzero"), False


def run_gate(gate: Gate, path: "str | os.PathLike[str]", timeout: float = 10) -> GateResult:
    """Run `gate` on the output file at `path`; returns (passed, why).

    A command string has `{out}` replaced by the quoted file path and `{id}` by the file's name without its
    extension, and runs through the shell. An argument list runs without a shell, with the same two
    replacements in each item. A function receives the file's text and returns a bool or `(bool, why)`.
    Exit code 0, a true verdict, is the only pass. A nonzero exit, a timeout, an exception or a gate that
    cannot start all fail.
    """
    passed, why, _ = _check(gate, os.fspath(path), timeout)
    return GateResult(passed, why)


def gate_fingerprint(gate: Gate) -> str:
    """Hash of the gate itself: the command line and the contents of every existing file it names.

    A changed gate script changes the fingerprint even when the command line stays the same. For a
    function the fingerprint covers its name and its bytecode.
    """
    if callable(gate):
        h = hashlib.sha256(("%s.%s" % (getattr(gate, "__module__", ""), getattr(gate, "__qualname__", repr(gate)))).encode())
        code = getattr(gate, "__code__", None)
        if code is not None:
            h.update(b"\0code:")
            h.update(code.co_code)
            h.update(repr(code.co_consts).encode("utf-8", "replace"))
        return h.hexdigest()
    cmd = gate if isinstance(gate, str) else "\0".join(str(p) for p in gate)
    h = hashlib.sha256(cmd.encode("utf-8"))
    if isinstance(gate, str):
        tokens = re.findall(r'"([^"]+)"', gate) + re.findall(r"'([^']+)'", gate) + gate.split()
    else:
        tokens = [str(p) for p in gate]
    own = os.path.realpath(sys.executable).lower()
    for cand in sorted(set(tokens)):
        cand_clean = cand.strip('\'"')
        if os.path.isfile(cand_clean):
            try:
                if os.path.realpath(cand_clean).lower() == own:
                    continue  # the interpreter is not part of the gate's logic
                with open(cand_clean, "rb") as fh:
                    h.update(b"\0file:")
                    h.update(cand_clean.encode("utf-8"))
                    h.update(b"\0")
                    h.update(fh.read())
            except OSError:
                pass
    return h.hexdigest()
