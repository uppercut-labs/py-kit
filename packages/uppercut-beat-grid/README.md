# uppercut-beat-grid

Detects tempo, beats and downbeats in audio and writes them as a JSON beat grid with a frame index for every beat. It also checks or snaps cut times to a grid, and renders a synthesized music bed at an exact tempo together with the grid it was built on, so cuts can land on beats with no detection error. The grid data model, alignment and bed synthesis use only NumPy. WAV files load with the standard library; other formats need the `audio` extra, and the optional `librosa` extra adds librosa's beat tracker as a second detection backend.

## Install

```sh
pip install uppercut-beat-grid
pip install "uppercut-beat-grid[audio]"      # FLAC, OGG and float WAV input
pip install "uppercut-beat-grid[librosa]"    # librosa backend
```

## Example

```python
from uppercut_beat_grid import detect, align, make_grid, generate_bed, write_bed

result = detect("track.wav")                 # tempo, beats, downbeats, energy, onsets
print(result["tempo"], result["downbeats"][:4])

grid = make_grid(bpm=128, duration=15, fps=60)   # planned grid with frame indices
report = align([0.47, 0.94, 1.6], grid, tolerance_ms=17)
print(report.misses, [r.delta_ms for r in report.results])

bed = generate_bed(124, "intro:2,build:4,drop:4,outro:2", style="pulse", seed=3)
write_bed(bed, "sound/bed")                  # bed.wav, bed.stems/*.wav, bed.grid.json
```

Command line:

```sh
uppercut-beat-grid detect track.wav --json track.grid.json
uppercut-beat-grid grid --bpm 128 --duration 15 --fps 60 --json plan.json
uppercut-beat-grid align --cuts cuts.json --beats plan.json --tolerance-ms 17
uppercut-beat-grid bed --bpm 124 --sections intro:2,build:4,drop:4,outro:2 --out sound/bed
```

`align` exits with 1 when any cut misses. A cuts file is a JSON list of times in seconds or `{"cuts": [...]}`.

## Functions

| Function | What it does |
| --- | --- |
| `detect(audio, sr=None, backend="fit", bpm_hint=None, beats_per_bar=4)` | Tempo, beat times, downbeat phase and times, per-beat energy and onset times. `audio` is a path or a mono array (with `sr`). Backend `"fit"` uses NumPy; `"librosa"` needs the extra. Detected tempo can be off by a factor of two; `bpm_hint` pins it. |
| `fit_tempo(envelope, hop_seconds, bpm_hint=None)` | Constant-tempo and phase fit on an onset envelope: grid search over the half, same and double of the estimate, an octave prior around 120 BPM, and a least-squares refinement. |
| `onset_envelope(y, sr)` | Spectral-flux onset strength and its seconds per frame. |
| `pick_downbeat_phase(envelope, hop_seconds, beats, beats_per_bar)` | Which beat starts a bar, by onset strength. |
| `grid_from_detection(result, fps=60)` | A grid dict from a `detect` result. |
| `make_grid(bpm, duration, offset=0, fps=60, beats_per_bar=4)` | A planned grid: beats with `i`, `t`, `bar`, `beat`, `frame`, plus half-beats. |
| `save_grid(grid, path)`, `load_grid(path)`, `beat_times(grid)`, `frame_index(t, fps)` | JSON round trip and helpers. |
| `snap_to_grid(t, beats, reach=None)` | Nearest beat time, or `None` beyond `reach` seconds. |
| `align(cuts, beats, tolerance_ms=17, half=False, auto_offset=False)` | An `AlignReport` with each cut's distance to the nearest beat. `half` adds half-beats as targets. `auto_offset` removes the median delta first, for judging a delivered file whose detected beats lag by a constant. 17 ms is one frame at 60 fps. |
| `snap_cuts(cuts, beats, half=False, reach=None)` | Cut times moved to their nearest beats. |
| `generate_bed(bpm, sections, style="pulse", key="A", scale="minor", seed=3, fps=60, end_hit=False, gaps=(), rate=48000)` | A `Bed(stems, mix, grid, rate)`. Sections: `intro`, `build`, `groove`, `drop`, `break`, `outro`, `hold`. Styles: `pulse`, `lofi`, `drive`. Scales: `minor`, `major`, `dorian`. Each beat in `gaps` is preceded by half a beat of silence. |
| `write_bed(bed, out)` | Writes `out.wav` (24-bit stereo, peak -6 dBFS), `out.stems/*.wav` and `out.grid.json`. |
| `read_audio(path)`, `write_wav24(path, samples, rate)` | Audio file helpers. |

The same bed arguments always produce the same samples.

## Errors

| Error | Raised when |
| --- | --- |
| `ValueError` | A tempo, fps or tolerance is out of range, a section string is malformed, a gap is outside the bed, a style, key or scale is unknown, there are no beats to align to, or the audio is too short to analyse. |
| `ImportError` | The `librosa` backend or a non-WAV format is used without its extra installed. |

## License

MIT
