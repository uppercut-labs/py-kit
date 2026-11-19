#!/usr/bin/env python3
"""Release gate: block a push or publish when the repo, its history or a built package contains secrets or
strings from a private denylist.

The denylist is never stored in this repository. Point to it with RELEASE_GATE_DENYLIST or --denylist.
One case-insensitive regular expression per line; lines starting with # are ignored.
Commit author and committer emails must be listed in RELEASE_GATE_ALLOWED_EMAILS (comma-separated).

Usage:
  python tools/release_gate.py                       # working tree + git history + commit metadata
  python tools/release_gate.py --artifact dist/*.whl --artifact pkg.tgz
Exit code 0 means clean; 1 means a hit; 2 means the gate could not run.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

TEXT_LIMIT = 2_000_000


def load_patterns(path: str) -> list[re.Pattern]:
    pats = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            pats.append(re.compile(line, re.I))
    if not pats:
        raise SystemExit("release gate: denylist is empty")
    return pats


def scan_text(label: str, text: str, pats: list[re.Pattern], hits: list[str]):
    for n, line in enumerate(text.splitlines(), 1):
        for p in pats:
            m = p.search(line)
            if m:
                hits.append(f"{label}:{n}: matches denylist entry /{p.pattern}/ near {line.strip()[:120]!r}")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          check=True).stdout


def scan_tree(pats, hits):
    files = git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0")
    for f in filter(None, files):
        p = Path(f)
        if not p.is_file() or p.stat().st_size > TEXT_LIMIT:
            continue
        data = p.read_bytes()
        if b"\0" in data[:4096]:
            continue
        scan_text(f, data.decode("utf-8", "replace"), pats, hits)
        scan_text(f"(path) {f}", f, pats, hits)


def scan_history(pats, hits):
    try:
        meta = git("log", "--all", "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x1e")
    except subprocess.CalledProcessError:
        return  # no commits yet
    for rec in filter(None, meta.split("\x1e")):
        parts = rec.strip("\n").split("\x00")
        if len(parts) < 6:
            continue
        sha = parts[0][:10]
        allowed = {e.strip().lower() for e in os.environ.get("RELEASE_GATE_ALLOWED_EMAILS", "").split(",") if e.strip()}
        for email in (parts[2], parts[4]):
            if email and email.lower() not in allowed:
                hits.append(f"commit {sha}: author/committer email {email!r} is not in RELEASE_GATE_ALLOWED_EMAILS")
        scan_text(f"commit {sha} metadata", "\n".join(parts[1:]), pats, hits)
    patch = git("log", "--all", "-p", "--no-color", "--format=commit %H")
    cur = "history"
    buf: list[str] = []
    for line in patch.splitlines():
        if line.startswith("commit "):
            if buf:
                scan_text(cur, "\n".join(buf), pats, hits)
            cur, buf = f"history {line[7:17]}", []
        elif line.startswith("+") and not line.startswith("+++"):
            buf.append(line[1:])
    if buf:
        scan_text(cur, "\n".join(buf), pats, hits)


def scan_artifact(path: Path, pats, hits):
    def scan_member(name: str, data: bytes):
        scan_text(f"{path.name}!(path)", name, pats, hits)
        if len(data) > TEXT_LIMIT or b"\0" in data[:4096]:
            return
        scan_text(f"{path.name}!{name}", data.decode("utf-8", "replace"), pats, hits)

    if path.suffix in (".whl", ".zip"):
        with zipfile.ZipFile(path) as z:
            for i in z.infolist():
                if not i.is_dir():
                    scan_member(i.filename, z.read(i))
    elif path.name.endswith((".tgz", ".tar.gz")):
        with tarfile.open(path, "r:gz") as t:
            for m in t.getmembers():
                if m.isfile():
                    scan_member(m.name, t.extractfile(m).read())
    else:
        raise SystemExit(f"release gate: unsupported artifact {path}")


def run_gitleaks(binary: str, hits):
    for mode in (["git", "."], ["dir", "."]):
        r = subprocess.run([binary, *mode, "--no-banner", "--redact", "--exit-code", "3"], capture_output=True, text=True)
        if r.returncode == 3:
            hits.append(f"gitleaks {mode[0]}: secrets found\n{r.stdout[-2000:]}{r.stderr[-2000:]}")
        elif r.returncode not in (0, 3):
            raise SystemExit(f"release gate: gitleaks {mode[0]} failed: {r.stderr[-500:]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--denylist", default=os.environ.get("RELEASE_GATE_DENYLIST"))
    ap.add_argument("--gitleaks", default=os.environ.get("GITLEAKS") or shutil.which("gitleaks"))
    ap.add_argument("--artifact", action="append", default=[])
    ap.add_argument("--no-history", action="store_true")
    a = ap.parse_args()
    if not a.denylist or not Path(a.denylist).is_file():
        print("release gate: set RELEASE_GATE_DENYLIST to the private denylist file", file=sys.stderr)
        return 2
    if not a.gitleaks:
        print("release gate: gitleaks not found; set GITLEAKS or put it on PATH", file=sys.stderr)
        return 2
    pats = load_patterns(a.denylist)
    hits: list[str] = []
    scan_tree(pats, hits)
    if not a.no_history:
        scan_history(pats, hits)
    for art in a.artifact:
        scan_artifact(Path(art), pats, hits)
    run_gitleaks(a.gitleaks, hits)
    if hits:
        print(f"release gate: {len(hits)} problem(s)")
        for h in hits[:200]:
            print("  " + h)
        return 1
    print("release gate: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
