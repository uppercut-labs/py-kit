"""A runner that calls an external command, for models that sit behind a command line tool."""

from __future__ import annotations

import os
import subprocess
from typing import Callable, Mapping, Optional, Sequence

from ._wave import Call, HostError, Runner


def command_runner(
    build: Callable[[Call], Sequence[str]],
    *,
    stdin: bool = True,
    env: Optional[Mapping[str, str]] = None,
) -> Runner:
    """Make a runner from a function that turns a Call into an argument list.

    The command runs with the call's timeout. Its standard output, stripped, is the model output. A nonzero
    exit, a timeout or a command that cannot start raises HostError, so the wave counts it as a host failure
    and not as output that failed the gate. With `stdin=True` the piece text is sent on standard input as
    UTF-8; otherwise the function is expected to put the text in the argument list. `env` is added to the
    environment, and `PYTHONUTF8=1` is set unless `env` says otherwise, so a Python command reads UTF-8.
    """

    def run(call: Call) -> str:
        argv = list(build(call))
        environment = {**os.environ, "PYTHONUTF8": "1", **(env or {})}
        try:
            r = subprocess.run(argv, input=call.text if stdin else None, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=call.timeout, env=environment)
        except subprocess.TimeoutExpired:
            raise HostError("timed out after %s seconds" % call.timeout)
        except OSError as exc:
            raise HostError("%s: %s" % (type(exc).__name__, exc))
        if r.returncode != 0:
            raise HostError((r.stderr or "").strip()[:300] or "exit %d" % r.returncode)
        return r.stdout.strip()

    return run
