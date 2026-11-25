# py-kit

Small, focused Python packages from Uppercut Labs, each published on its own on PyPI with the `uppercut-` prefix.

| Package | What it does |
| --- | --- |
| [`uppercut-beat-grid`](packages/uppercut-beat-grid) | Detect beats, downbeats and tempo into a JSON beat grid, align cut times to it, and render a beat-synced music bed |
| [`uppercut-contact-sheet`](packages/uppercut-contact-sheet) | Build labelled contact-sheet grids from images and video frames with Pillow |
| [`uppercut-grounded-answers`](packages/uppercut-grounded-answers) | Answer from retrieved evidence: decode a model's JSON reply, reject citations that were never supplied, raise typed provider failures that say whether to fail over, and try providers in order |
| [`uppercut-ffmpeg-loudness`](packages/uppercut-ffmpeg-loudness) | Measure integrated loudness and true peak with ffmpeg, then apply gain or normalize to a target |
| [`uppercut-json-records`](packages/uppercut-json-records) | Canonical JSON and SHA-256 hashes, stable prefixed IDs, JSON and JSONL batch loading with clear errors, and JSON Schema validators (jsonschema is an optional extra) |
| [`uppercut-http-retry`](packages/uppercut-http-retry) | Retry HTTP calls on 429 and 5xx with exponential backoff and Retry-After, and poll an asynchronous operation until it finishes; works with urllib, requests and httpx |
| [`uppercut-loopback-api`](packages/uppercut-loopback-api) | A small JSON API on 127.0.0.1 for a private app behind Tailscale Serve: Host allowlist, strict bodies, redacted request log, exclusive port bind, optional PWA client |
| [`uppercut-pixel-art-sheets`](packages/uppercut-pixel-art-sheets) | Pillow helpers for indexed pixel-art sheets: palettes, pattern fills, a bitmap font and sheet packing |
| [`uppercut-presentmon-measure`](packages/uppercut-presentmon-measure) | Run startup, idle and workload scenarios against a desktop app, record frame timing with Intel PresentMon and summarize it to JSON |
| [`uppercut-rawvideo-encoder`](packages/uppercut-rawvideo-encoder) | Pipe RGB frames from Pillow renders to ffmpeg as H.264, check ffmpeg's exit code, plus a nearest-neighbour upscale and a cached font loader |
| [`uppercut-subprocess-runner`](packages/uppercut-subprocess-runner) | Run a subprocess with a timeout, an output cap and cancellation, and kill its whole process tree |
| [`uppercut-wave-gate`](packages/uppercut-wave-gate) | Run one prompt over many pieces with a pluggable model runner, count only output that passes a gate, resume safely, and refuse to start unless the gate rejects a known-bad sample |
| [`uppercut-windows-window-capture`](packages/uppercut-windows-window-capture) | Capture one window on Windows with PrintWindow, as a Python module and a command line tool, to PNG or BMP |

Game development packages, `uppercut-pixel-art-sheets` and `uppercut-sound-effect-synth` among them, live in [game-kit](https://github.com/uppercut-labs/game-kit). They keep their PyPI names, so installing them is unchanged.

## Working in this repo

```sh
uv sync --all-packages
uv run pytest
```

Each package lives in `packages/<name>` with its own `pyproject.toml`, `README.md`, `src/` and `tests/`.

See [RELEASING.md](RELEASING.md) for how a version is published.

## License

MIT
