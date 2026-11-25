"""The worker protocol: one JSON object per line in each direction.

A worker prints one ready line (`{"ready": true, ...}`) when its model is loaded. Then, for every request line
on its standard input, it writes one response line on its standard output. Anything else it prints to standard
output that is not a JSON object is ignored by the reader, so a library's stray log line does not break a job.

Request:  {"text": str, "output_path": str, "seed": int | null, "settings": {...}, "voice_id": str (optional)}
Response: {"ok": bool, "sample_rate": int, "samples": int, "wall_s": float, "error": str | null, ...}
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional, TextIO


def make_request(text: str, output_path: str, *, seed: Optional[int] = None,
                 settings: Optional[Mapping[str, Any]] = None, voice_id: Optional[str] = None) -> Dict[str, Any]:
    """A request object. `voice_id` is left out when empty, so a worker with one fixed voice never sees it."""
    request: Dict[str, Any] = {"text": text, "output_path": output_path, "seed": seed, "settings": dict(settings or {})}
    if voice_id:
        request["voice_id"] = voice_id
    return request


def read_message(stream: TextIO) -> Optional[Dict[str, Any]]:
    """The next JSON object on `stream`, skipping lines that are not one. None at end of stream."""
    while True:
        line = stream.readline()
        if not line:
            return None
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict):
            return message


def write_message(stream: TextIO, message: Mapping[str, Any]) -> None:
    """Write one message as a line and flush, so the other side sees it at once."""
    stream.write(json.dumps(message, ensure_ascii=False) + "\n")
    stream.flush()
