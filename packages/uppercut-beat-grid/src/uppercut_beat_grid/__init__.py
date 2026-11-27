"""Beat grids: detect them, build them, align cuts to them, and render a bed on one."""

from .align import AlignReport, CutResult, align, cut_times, snap_cuts
from .audio import read_audio, write_wav24
from .bed import Bed, generate_bed, parse_sections, write_bed
from .detect import detect, fit_tempo, grid_from_detection, onset_envelope, pick_downbeat_phase
from .grid import beat_times, frame_index, load_grid, make_grid, save_grid, snap_to_grid, with_half_beats

__all__ = [
    "AlignReport",
    "Bed",
    "CutResult",
    "align",
    "beat_times",
    "cut_times",
    "detect",
    "fit_tempo",
    "frame_index",
    "generate_bed",
    "grid_from_detection",
    "load_grid",
    "make_grid",
    "onset_envelope",
    "parse_sections",
    "pick_downbeat_phase",
    "read_audio",
    "save_grid",
    "snap_cuts",
    "snap_to_grid",
    "with_half_beats",
    "write_bed",
    "write_wav24",
]
__version__ = "1.0.0"
