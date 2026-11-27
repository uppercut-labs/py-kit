"""Run a subprocess with a timeout, an output cap and cancellation, and kill its whole process tree."""

from ._kill import KillInfo, kill_tree, popen_group, popen_group_kwargs
from ._runner import CommandError, RunResult, run

__all__ = [
    "CommandError",
    "KillInfo",
    "RunResult",
    "kill_tree",
    "popen_group",
    "popen_group_kwargs",
    "run",
]
__version__ = "1.0.0"
