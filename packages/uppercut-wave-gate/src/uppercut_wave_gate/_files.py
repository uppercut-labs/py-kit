"""Piece loading and atomic file writes."""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Dict, List

SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


class WaveError(Exception):
    """A run that cannot start or continue: bad usage, a refused resume, or a gate that proves nothing.

    The command line turns it into exit code 2.
    """

    exit_code = 2


def write_atomic(path: str, text: str) -> None:
    """Write `text` to `path` through a temporary file in the same folder, then rename it into place."""
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def load_pieces(path: "str | os.PathLike[str]") -> List[Dict[str, Any]]:
    """Read a JSON Lines file of `{"id": ..., "text": ...}` pieces.

    Ids must be filename safe (letters, digits, `.`, `_`, `-`, at most 120 characters), unique, and every
    piece needs non-empty text. Blank lines are skipped. Raises WaveError otherwise.
    """
    pieces: List[Dict[str, Any]] = []
    seen = set()
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                piece = json.loads(line)
            except json.JSONDecodeError as exc:
                raise WaveError("%s line %d is not JSON: %s" % (path, n, exc.msg))
            if not isinstance(piece, dict) or not SAFE_ID.match(str(piece.get("id", ""))) \
                    or not str(piece.get("text", "")).strip():
                raise WaveError("%s line %d needs a filename-safe id and non-empty text" % (path, n))
            if piece["id"] in seen:
                raise WaveError("duplicate id %s" % piece["id"])
            seen.add(piece["id"])
            pieces.append(piece)
    return pieces
