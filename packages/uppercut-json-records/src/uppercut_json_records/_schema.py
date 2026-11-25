"""JSON Schema validators. Needs the `schema` extra (jsonschema); nothing else in the package does."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Type, Union

from ._records import JsonRecordsError


def _jsonschema() -> Any:
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "load_validator and validate_value need jsonschema: pip install 'uppercut-json-records[schema]'"
        ) from exc
    return jsonschema


def load_validator(schema: Union[str, Path, Mapping[str, Any]], *, formats: bool = True) -> Any:
    """A Draft 2020-12 validator for a schema file (path) or a schema already loaded (dict).

    The schema itself is checked first, so a broken schema fails here and not on the first record.
    `formats=True` also checks `format` keywords such as `email` and `ipv4`, as far as jsonschema can (some formats need extra jsonschema packages).
    """
    jsonschema = _jsonschema()
    if isinstance(schema, (str, Path)):
        schema = json.loads(Path(schema).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    checker = jsonschema.FormatChecker() if formats else None
    return jsonschema.Draft202012Validator(schema, format_checker=checker)


def _path_key(error: Any) -> Any:
    return list(error.path)


def validate_value(validator: Any, value: Any, label: str, *, error: Type[Exception] = JsonRecordsError) -> None:
    """Raise `error("Invalid {label} at {location}: {message}")` for the first schema violation.

    Violations are ordered by their path in the document, so the same bad value always reports the
    same one first. `location` is the dotted path, or the label when the value itself is wrong.
    """
    problems = list(validator.iter_errors(value))
    if not problems:
        return
    try:
        problems.sort(key=_path_key)
    except TypeError:  # an array index compared with an object key
        problems.sort(key=lambda problem: [str(part) for part in problem.path])
    first = problems[0]
    location = ".".join(str(part) for part in first.path) or label
    raise error(f"Invalid {label} at {location}: {first.message}")
