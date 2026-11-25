"""Behaviour of uppercut-json-records, checked against the helper copies it replaces.

The `copy_*` functions are the hand-written helpers that several importer modules carry (a batch
loader, a JSON object reader, a canonical hash, a stable ID, a schema validator and its error
reporter), copied with only the exception name changed. The package must give the same answers and
the same messages on the same inputs; a few cases pin where it deliberately differs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from uppercut_json_records import (
    JsonRecordsError,
    canonical_hash,
    canonical_json,
    load_records,
    load_validator,
    read_json_object,
    stable_id,
    validate_value,
)


class BatchError(ValueError):
    """The project's own error type, passed to the package as `error=`."""


# --- the copies --------------------------------------------------------------------------------------------


def copy_stable(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"


def copy_canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def copy_default_ascii_hash(value: Any) -> str:
    # the fingerprint style that leaves ensure_ascii at its default
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def copy_load_items(batch_path: Path) -> list:
    if batch_path.suffix.lower() not in {".jsonl", ".json"}:
        raise BatchError(f"Unsupported Exported Batch format: {batch_path.suffix or '<none>'}")
    try:
        if batch_path.suffix.lower() == ".jsonl":
            return [
                json.loads(line)
                for line in batch_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        value = json.loads(batch_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BatchError(f"Unable to read Exported Batch: {error}") from error
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("items", "emails"):
            if isinstance(value.get(key), list):
                return value[key]
    raise BatchError(
        "Supported JSON Exported Batch must be an array or an object with an items or emails array"
    )


def copy_read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BatchError(f"Unable to read {label} {path}: {error}") from error
    if not isinstance(value, dict):
        raise BatchError(f"{label} {path} must contain a JSON object")
    return value


jsonschema = pytest.importorskip("jsonschema")


def copy_validator(path: Path):
    schema = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


def copy_validate(validator, value: dict, label: str) -> None:
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.path))
    if errors:
        location = ".".join(str(part) for part in errors[0].path) or label
        raise BatchError(f"Invalid {label} at {location}: {errors[0].message}")


def outcome(call):
    try:
        return ("ok", call())
    except BatchError as error:
        return ("error", str(error))


# --- canonical JSON and hashes -----------------------------------------------------------------------------

VALUES = [
    {},
    [],
    None,
    "text",
    {"b": 1, "a": [3, 2, {"z": None, "y": True}]},
    {"name": "Café ☃ \U0001f600", "n": 1.5, "big": 12345678901234567890, "neg": -0.0},
    {"nested": {"k2": "v", "k1": "w"}, "list": [{"b": 1, "a": 2}]},
    {"quote": 'he said "hi"\n\ttab', "slash": "a/b\\c"},
]


@pytest.mark.parametrize("value", VALUES)
def test_canonical_hash_matches_the_copy(value):
    assert canonical_hash(value) == copy_canonical_hash(value)


@pytest.mark.parametrize("value", VALUES)
def test_ascii_escaped_hash_matches_the_default_json_copy(value):
    assert canonical_hash(value, prefix="", ensure_ascii=True) == copy_default_ascii_hash(value)


def test_key_order_does_not_change_the_hash_and_content_does():
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})
    assert canonical_hash({"a": 1}, prefix="rev:").startswith("rev:")


def test_canonical_json_is_compact_sorted_and_ascii_on_request():
    assert canonical_json({"b": [1, 2], "a": "é"}) == '{"a":"é","b":[1,2]}'
    assert canonical_json({"a": "é"}, ensure_ascii=True) == '{"a":"\\u00e9"}'


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), {"x": float("-inf")}])
def test_non_finite_numbers_are_refused_instead_of_hashed(bad):
    with pytest.raises(ValueError):
        canonical_json(bad)
    with pytest.raises(ValueError):
        canonical_hash(bad)


@pytest.mark.parametrize("prefix, value", [("evt", "a"), ("opp", ""), ("x", "Café ☃"), ("fact", "k" * 5000)])
def test_stable_id_matches_the_copy(prefix, value):
    assert stable_id(prefix, value) == copy_stable(prefix, value)


def test_stable_id_length_is_adjustable_and_checked():
    assert stable_id("a", "b", length=8) == copy_stable("a", "b")[:2 + 8]
    assert len(stable_id("a", "b", length=64)) == 2 + 64
    for length in (0, 65, -1):
        with pytest.raises(ValueError):
            stable_id("a", "b", length=length)


# --- loading batches ---------------------------------------------------------------------------------------


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


BATCH_FILES = {
    "list.json": '[{"id": 1}, {"id": 2}]',
    "items.json": '{"items": [{"id": 1}], "emails": [{"id": 9}]}',
    "emails.json": '{"emails": [{"id": 5}], "other": 1}',
    "empty-items.json": '{"items": [], "emails": [{"id": 5}]}',
    "items-not-list.json": '{"items": {"id": 1}, "emails": [{"id": 6}]}',
    "none.json": '{"rows": [1]}',
    "scalar.json": '"just text"',
    "broken.json": '{"items": [',
    "lines.jsonl": '{"id": 1}\n\n  \n{"id": 2}\n',
    "lines-with-scalars.jsonl": '1\n"two"\n[3]\n',
    "bad-line.jsonl": '{"id": 1}\nnot json\n',
    "empty.jsonl": "",
    "UPPER.JSONL": '{"id": 3}\n',
    "notes.txt": "[]",
    "noext": "[]",
    "missing.json": None,
}


@pytest.mark.parametrize("name", sorted(BATCH_FILES))
def test_load_records_matches_the_copy_for_every_shape(tmp_path, name):
    if BATCH_FILES[name] is not None:
        write(tmp_path, name, BATCH_FILES[name])
    path = tmp_path / name
    expected = outcome(lambda: copy_load_items(path))
    actual = outcome(
        lambda: load_records(path, label="Exported Batch", wrappers=("items", "emails"), error=BatchError)
    )
    if expected[0] == "error":
        # the copy puts the OS or parser text after a fixed prefix; the path is in the OS text only
        assert actual == expected
    else:
        assert actual == expected


def test_load_records_defaults_raise_the_package_error_and_name_the_batch(tmp_path):
    path = write(tmp_path, "b.json", '{"rows": []}')
    with pytest.raises(JsonRecordsError, match=r"^Supported JSON batch must be an array or an object with an items array$"):
        load_records(path)
    with pytest.raises(JsonRecordsError, match=r"^Unsupported batch format: \.csv$"):
        load_records(tmp_path / "b.csv")
    assert isinstance(JsonRecordsError("x"), ValueError)


def test_load_records_without_wrapper_keys_accepts_only_arrays(tmp_path):
    assert load_records(write(tmp_path, "a.json", "[1]"), wrappers=()) == [1]
    with pytest.raises(JsonRecordsError, match="must be an array$"):
        load_records(write(tmp_path, "o.json", '{"items": [1]}'), wrappers=())


def test_load_records_also_reports_undecodable_bytes_as_an_unreadable_batch(tmp_path):
    path = tmp_path / "bytes.json"
    path.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(JsonRecordsError, match="^Unable to read batch: "):
        load_records(path)


# --- reading one JSON object -------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ['{"a": 1}', "[1]", "7", "nope", "", None])
def test_read_json_object_matches_the_copy(tmp_path, text):
    path = tmp_path / "doc.json"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    expected = outcome(lambda: copy_read_json(path, "Workspace"))
    actual = outcome(lambda: read_json_object(path, "Workspace", error=BatchError))
    assert actual == expected


def test_read_json_object_uses_the_package_error_by_default(tmp_path):
    with pytest.raises(JsonRecordsError, match="JSON file .* must contain a JSON object"):
        read_json_object(write(tmp_path, "x.json", "[]"))


# --- schema validators -------------------------------------------------------------------------------------

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["id", "contact", "tags"],
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "pattern": "^rec_[0-9a-f]{4}$"},
        "contact": {"type": "string", "format": "email"},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
        "meta": {"type": "object", "properties": {"n": {"type": "integer", "minimum": 0}}},
    },
}

GOOD = {"id": "rec_00ff", "contact": "someone@example.test", "tags": ["a"]}
INVALID = [
    {},
    {**GOOD, "id": "nope"},
    {**GOOD, "contact": "no-at-sign"},
    {**GOOD, "tags": ["a", "b", "c"]},
    {**GOOD, "tags": ["a", 2]},
    {**GOOD, "extra": 1},
    {**GOOD, "meta": {"n": -1}},
    {"id": 5, "contact": 5, "tags": 5},
]


@pytest.fixture
def schema_path(tmp_path):
    return write(tmp_path, "schema.json", json.dumps(SCHEMA))


def test_validator_accepts_a_good_record_and_matches_the_copy_on_bad_ones(schema_path):
    package = load_validator(schema_path)
    copy = copy_validator(schema_path)
    validate_value(package, GOOD, "record", error=BatchError)
    copy_validate(copy, GOOD, "record")
    for value in INVALID:
        expected = outcome(lambda: copy_validate(copy, value, "record"))
        actual = outcome(lambda: validate_value(package, value, "record", error=BatchError))
        assert actual == expected
        assert actual[0] == "error"


def test_a_schema_dict_works_like_a_path_and_a_broken_schema_fails_at_load(schema_path):
    validator = load_validator(SCHEMA)
    with pytest.raises(JsonRecordsError, match="^Invalid record at id:"):
        validate_value(validator, {**GOOD, "id": "x"}, "record")
    with pytest.raises(jsonschema.SchemaError):
        load_validator({"type": "not-a-type"})


def test_formats_can_be_turned_off(schema_path):
    strict, loose = load_validator(schema_path), load_validator(schema_path, formats=False)
    bad_contact = {**GOOD, "contact": "no-at-sign"}
    with pytest.raises(JsonRecordsError):
        validate_value(strict, bad_contact, "record")
    validate_value(loose, bad_contact, "record")


def test_errors_are_reported_in_document_order_even_when_paths_mix_indexes_and_keys():
    schema = {"type": "object", "properties": {
        "list": {"type": "array", "items": {"type": "integer"}},
        "name": {"type": "string"},
    }}
    validator = load_validator(schema)
    value = {"list": [1, "x", "y"], "name": 7}
    with pytest.raises(JsonRecordsError) as caught:
        validate_value(validator, value, "doc")
    assert str(caught.value).startswith("Invalid doc at list.1: ")


def test_the_location_is_the_label_when_the_value_itself_is_wrong():
    with pytest.raises(JsonRecordsError, match=r"^Invalid thing at thing: "):
        validate_value(load_validator({"type": "object"}), [], "thing")
