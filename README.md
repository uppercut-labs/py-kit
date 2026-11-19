# py-kit

Small, focused Python packages from Uppercut Labs, each published on its own on PyPI with the `uppercut-` prefix.

| Package | What it does |
| --- | --- |

## Working in this repo

```sh
uv sync --all-packages
uv run pytest
```

Each package lives in `packages/<name>` with its own `pyproject.toml`, `README.md`, `src/` and `tests/`.

See [RELEASING.md](RELEASING.md) for how a version is published.

## License

MIT
