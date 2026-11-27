"""The protocol for a resident text-to-speech worker process, a GPU lock that queues, and a resumable job runner."""

from .job import SAFE_ID, main, run_job
from .lock import GpuLock, LockBusy, process_alive
from .priority import raise_tree_priority
from .protocol import make_request, read_message, write_message
from .serve import serve
from .wav import wav_seconds
from .worker import WorkerError, WorkerProcess

__all__ = [
    "GpuLock",
    "LockBusy",
    "SAFE_ID",
    "WorkerError",
    "WorkerProcess",
    "main",
    "make_request",
    "process_alive",
    "raise_tree_priority",
    "read_message",
    "run_job",
    "serve",
    "wav_seconds",
    "write_message",
]
__version__ = "1.0.0"
