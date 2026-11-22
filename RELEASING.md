# Releasing

Every package is versioned and published on its own. A release only goes out after the release gate passes on the source tree, the git history and the exact files that will be uploaded.

1. Bump `version` in `packages/<name>/pyproject.toml`.
2. `uv sync --all-packages && uv run pytest`
3. `uv build --package <name> --out-dir dist/<name>`
4. `python tools/release_gate.py --artifact dist/<name>/<wheel>.whl --artifact dist/<name>/<sdist>.tar.gz`
   - The gate needs `RELEASE_GATE_DENYLIST` (a private file of strings that must never ship, kept outside this repo) and `gitleaks` on `PATH` or in `GITLEAKS`.
   - The gate also needs `RELEASE_GATE_ALLOWED_EMAILS`, a comma-separated list of the commit author and committer emails it accepts. Any other address in the history fails the gate. For this repository: `packages@uppercut-labs.com,127160473+devin-thomas@users.noreply.github.com,noreply@github.com` (the last is the committer GitHub records on merged pull requests).
   - Exit code 0 is the only passing result.
5. `uv publish dist/<name>/*` with the PyPI API token in `UV_PUBLISH_TOKEN`.
6. Tag the commit `<name>@<version>` and push the tag.

Releases are made from a maintainer machine. This repository does not use hosted CI for publishing.
