"""Canonical JSON, hashes, stable IDs and file loading. Standard library only."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, List, Type, Union


class JsonRecordsError(ValueError):
    """A file could not be read as the JSON it should hold, or a value failed its schema.

    Every function that raises it takes an `error` argument, so a project can have its own
    exception type (and keep its own tests and handlers) with the same messages.
    """


def canonical_json(value: Any, *, ensure_ascii: bool = False) -> str:
    """The one JSON text for a value: keys sorted, no spaces, `NaN` and `Infinity` refused.

    Two equal values always give the same text, which is what makes it safe to hash.
    `ensure_ascii=True` writes non-ASCII as `\\uXXXX` escapes; the default keeps the characters.
    """
    return json.dumps(value, ensure_ascii=ensure_ascii, separators=(",", ":"), sort_keys=True, allow_nan=False)


def canonical_hash(value: Any, *, prefix: str = "sha256:", ensure_ascii: bool = False) -> str:
    """`prefix` plus the SHA-256 hex digest of the value's canonical JSON, encoded as UTF-8."""
    return prefix + hashlib.sha256(canonical_json(value, ensure_ascii=ensure_ascii).encode("utf-8")).hexdigest()


def stable_id(prefix: str, value: str, *, length: int = 16) -> str:
    """`prefix_` plus the first `length` hex digits of the SHA-256 of `value`: the same input, the same ID."""
    if not 1 <= length <= 64:
        raise ValueError("length must be between 1 and 64")
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:length]}"


def read_json_object(
    path: Union[str, Path], label: str = "JSON file", *, error: Type[Exception] = JsonRecordsError
) -> dict:
    """Read a UTF-8 JSON file that must hold one object.

    Raises `error("Unable to read {label} {path}: ...")` when it cannot be read or parsed and
    `error("{label} {path} must contain a JSON object")` when it holds anything else.
    """
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise error(f"Unable to read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise error(f"{label} {path} must contain a JSON object")
    return value


def load_records(
    path: Union[str, Path],
    *,
    label: str = "batch",
    wrappers: Iterable[str] = ("items",),
    error: Type[Exception] = JsonRecordsError,
) -> List[Any]:
    """Load a batch of records from a `.json` or `.jsonl` file.

    `.jsonl` holds one JSON value per line (blank lines are skipped). `.json` holds an array, or an object
    with one of the `wrappers` keys (the first one that holds an array wins). Anything else raises
    `error` with a message that starts with `Unsupported {label} format`, `Unable to read {label}`
    or `Supported JSON {label} must be an array or an object with ...`. Items are returned as parsed;
    checking them is left to the caller.
    """
    path = Path(path)
    keys = tuple(wrappers)
    suffix = path.suffix.lower()
    if suffix not in {".jsonl", ".json"}:
        raise error(f"Unsupported {label} format: {path.suffix or '<none>'}")
    try:
        text = path.read_text(encoding="utf-8")
        if suffix == ".jsonl":
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        value = json.loads(text)
    except (OSError, ValueError) as exc:
        raise error(f"Unable to read {label}: {exc}") from exc
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in keys:
            if isinstance(value.get(key), list):
                return value[key]
    shape = "an array" + (f" or an object with an {' or '.join(keys)} array" if keys else "")
    raise error(f"Supported JSON {label} must be {shape}")
