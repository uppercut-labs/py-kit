"""The worker side of the protocol: wrap any synthesis function as a worker process."""

from __future__ import annotations

import sys
import time
from typing import Any, Callable, Dict, Mapping, Optional, TextIO

from .protocol import read_message, write_message

Synthesize = Callable[[Dict[str, Any]], Mapping[str, Any]]


def serve(synthesize: Synthesize, *, ready: Optional[Mapping[str, Any]] = None,
          stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
    """Run the worker loop: announce readiness, answer each request line, stop at end of input.

    `synthesize(request)` writes the audio to `request["output_path"]` and returns a dict such as
    `{"sample_rate": 24000, "samples": 48000}`. The loop adds `ok` and `wall_s`. An exception it raises becomes
    `{"ok": false, "error": "<type>: <message>"}` and the loop carries on with the next request, so one bad
    sentence does not end the job. `ready` is merged into the first line (`{"ready": true, ...}`): put the
    device and the data type there. Returns 0.
    """
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    write_message(stdout, {"ready": True, **(ready or {})})
    while True:
        request = read_message(stdin)
        if request is None:
            return 0
        started = time.time()
        try:
            result = dict(synthesize(request))
            response: Dict[str, Any] = {"ok": True, **result}
        except Exception as exc:
            response = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
        response.setdefault("wall_s", round(time.time() - started, 3))
        write_message(stdout, response)
