# py-kit

Small, focused Python packages from Uppercut Labs, each published on its own on PyPI with the `uppercut-` prefix.

| Package | What it does |
| --- | --- |
| [`uppercut-beat-grid`](packages/uppercut-beat-grid) | Detect beats, downbeats and tempo into a JSON beat grid, align cut times to it, and render a beat-synced music bed |
| [`uppercut-contact-sheet`](packages/uppercut-contact-sheet) | Build labelled contact-sheet grids from images and video frames with Pillow |
| [`uppercut-ffmpeg-loudness`](packages/uppercut-ffmpeg-loudness) | Measure integrated loudness and true peak with ffmpeg, then apply gain or normalize to a target |
| [`uppercut-pixel-art-sheets`](packages/uppercut-pixel-art-sheets) | Pillow helpers for indexed pixel-art sheets: palettes, pattern fills, a bitmap font and sheet packing |
| [`uppercut-presentmon-measure`](packages/uppercut-presentmon-measure) | Run startup, idle and workload scenarios against a desktop app, record frame timing with Intel PresentMon and summarize it to JSON |
| [`uppercut-rawvideo-encoder`](packages/uppercut-rawvideo-encoder) | Pipe RGB frames from Pillow renders to ffmpeg as H.264, check ffmpeg's exit code, plus a nearest-neighbour upscale and a cached font loader |
| [`uppercut-sound-effect-synth`](packages/uppercut-sound-effect-synth) | Deterministic procedural sound effects: envelopes, filtered noise, sweeps, seeded variation and WAV output |
| [`uppercut-subprocess-runner`](packages/uppercut-subprocess-runner) | Run a subprocess with a timeout, an output cap and cancellation, and kill its whole process tree |
| [`uppercut-windows-window-capture`](packages/uppercut-windows-window-capture) | Capture one window on Windows with PrintWindow, as a Python module and a command line tool, to PNG or BMP |

## Working in this repo

```sh
uv sync --all-packages
uv run pytest
```

Each package lives in `packages/<name>` with its own `pyproject.toml`, `README.md`, `src/` and `tests/`.

See [RELEASING.md](RELEASING.md) for how a version is published.

## License

MIT
