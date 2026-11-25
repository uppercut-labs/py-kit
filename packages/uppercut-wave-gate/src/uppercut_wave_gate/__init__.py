"""Run one prompt over many pieces, count only output that passes a gate, and refuse to start with a gate that proves nothing."""

from ._files import SAFE_ID, WaveError, load_pieces, write_atomic
from ._gate import Gate, GateResult, gate_fingerprint, run_gate
from ._metrics import output_diversity, summarize_samples
from ._runner import command_runner
from ._wave import Call, HostError, Runner, Wave, WaveConfig, Worker, parse_workers, run_wave
from .cli import add_wave_arguments, config_from_args, main

__all__ = [
    "Call",
    "Gate",
    "GateResult",
    "HostError",
    "Runner",
    "SAFE_ID",
    "Wave",
    "WaveConfig",
    "WaveError",
    "Worker",
    "add_wave_arguments",
    "command_runner",
    "config_from_args",
    "gate_fingerprint",
    "load_pieces",
    "main",
    "output_diversity",
    "parse_workers",
    "run_gate",
    "run_wave",
    "summarize_samples",
    "write_atomic",
]
__version__ = "0.1.0"
