# uppercut-json-records

The small helpers every JSON-records importer ends up writing for itself: a canonical JSON text and hash for a value, stable prefixed IDs, loading a batch from a `.json` or `.jsonl` file with clear errors, reading a file that must hold one object, and building a JSON Schema validator whose first error reads well. The hashing and loading are standard library only. Schema validation needs `jsonschema`, which is an extra.

```sh
pip install uppercut-json-records            # canonical JSON, hashes, IDs, loading
pip install "uppercut-json-records[schema]"  # plus load_validator and validate_value
```

```python
from pathlib import Path
from uppercut_json_records import (
    canonical_hash, load_records, load_validator, read_json_object, stable_id, validate_value,
)

class ImportFailed(ValueError):   # your own error type; the package raises it for you
    pass

items = load_records(Path("batch.jsonl"), label="export", wrappers=("items", "emails"), error=ImportFailed)

validator = load_validator(Path("schemas/record.json"))
for item in items:
    validate_value(validator, item, "record", error=ImportFailed)   # Invalid record at tags.1: 2 is not of type 'string'

revision = canonical_hash(items[0])        # "sha256:" + hex digest, the same for any key order
record_id = stable_id("rec", "source-17")  # "rec_" + 16 hex digits, the same every run
settings = read_json_object(Path("settings.json"), "settings", error=ImportFailed)
```

## API

| Name | What it does |
| --- | --- |
| `canonical_json(value, *, ensure_ascii=False)` | The one JSON text for a value: keys sorted, no spaces, `NaN` and `Infinity` refused (`ValueError`) |
| `canonical_hash(value, *, prefix="sha256:", ensure_ascii=False)` | `prefix` plus the SHA-256 hex digest of the canonical JSON as UTF-8. Use `prefix=""` for a bare digest |
| `stable_id(prefix, value, *, length=16)` | `prefix_` plus the first `length` hex digits of the SHA-256 of a string |
| `load_records(path, *, label="batch", wrappers=("items",), error=JsonRecordsError)` | The records in a `.json` array, in an array under one of the `wrappers` keys of a `.json` object, or one per line of a `.jsonl` file (blank lines skipped) |
| `read_json_object(path, label="JSON file", *, error=JsonRecordsError)` | A UTF-8 JSON file that must hold one object |
| `load_validator(schema, *, formats=True)` | A Draft 2020-12 validator from a schema path or dict. Checks the schema first. Needs the `schema` extra |
| `validate_value(validator, value, label, *, error=JsonRecordsError)` | Raises `error("Invalid {label} at {path}: {message}")` for the first violation in document order |
| `JsonRecordsError` | The default error, a `ValueError`. Every function that raises takes `error=` to raise your own type with the same message |

## Notes

- Hash the same thing the same way everywhere. `ensure_ascii` changes the bytes that are hashed: a project that already stores hashes made with `json.dumps(..., sort_keys=True, separators=(",", ":"))` (which escapes non-ASCII) needs `ensure_ascii=True` and `prefix=""` to get the same values; the default matches `ensure_ascii=False` with a `sha256:` prefix.
- `NaN` and `Infinity` are not JSON. A hand-written `json.dumps` writes them anyway and hashes the result; `canonical_json` raises instead, so a hash can never depend on a value no other JSON reader accepts.
- `.jsonl` and `.json` suffixes are matched without regard to case. Files are read as UTF-8, and bytes that are not UTF-8 are reported as an unreadable batch.
- A `.json` object is unwrapped through the first of `wrappers` that holds an array; an empty array counts. Items are returned as parsed. Checking them is the caller's job (that is what `validate_value` is for).
- Messages: `Unsupported {label} format: .csv`, `Unable to read {label}: {parser message}`, `Supported JSON {label} must be an array or an object with an items or emails array`, `Unable to read {label} {path}: ...`, `{label} {path} must contain a JSON object`.

## License

MIT
