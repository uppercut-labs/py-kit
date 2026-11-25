"""Behaviour of uppercut-redact, checked against the three scrubbers it replaces.

Every secret below is a made-up word, never a real credential shape. The `copy_*` functions are the
hand-written scrubbers the package replaces: a `key: value` pattern, a bearer and token pattern, and a
sensitive-key payload masker. The package must remove everything those copies removed, and the cases
where a copy let a secret through are listed on purpose.
"""

from __future__ import annotations

import re

import pytest

from uppercut_redact import PLACEHOLDER, SENSITIVE_WORDS, is_sensitive_key, redact_mapping, redact_text

# --- the copies ----------------------------------------------------------------------------------------------

COPY_SECRET_PATTERN = re.compile(r"(?i)(authorization|token|secret|password|api[_-]?key)(\s*[:=]\s*)(\S+)")


def copy_key_value(value: str) -> str:
    return COPY_SECRET_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)


def copy_bearer(value: str) -> str:
    redacted = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", value)
    return re.sub(r"(?i)(access[_ -]?token|refresh[_ -]?token)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", redacted)


COPY_PAYLOAD_KEYS = re.compile(r"(?i)(token|authorization|password|secret|body|subject|url|attachment|note|email)")
COPY_TELEMETRY_KEYS = re.compile(r"(?i)(token|authorization|subject|body|attachment|note|url|email|password|secret)")


def copy_payload(payload: dict) -> dict:
    result: dict = {}
    for key, value in payload.items():
        if COPY_PAYLOAD_KEYS.search(key):
            result[key] = "[REDACTED]"
        elif isinstance(value, dict):
            result[key] = copy_payload(value)
        elif isinstance(value, list):
            result[key] = [copy_payload(item) if isinstance(item, dict) else item for item in value]
        elif isinstance(value, str):
            result[key] = value[:256]
        else:
            result[key] = value
    return result


# --- text: (input, secrets that must not survive) ----------------------------------------------------------------

CASES = [
    ("Authorization: Bearer tok-alpha access_token=tok-beta refresh-token: tok-gamma", ["tok-alpha", "tok-beta", "tok-gamma"]),
    ("password=pw-one; next step", ["pw-one"]),
    ("password: pw-two", ["pw-two"]),
    ("api_key=key-one&page=2", ["key-one"]),
    ("api-key: key-two", ["key-two"]),
    ("apikey=key-three", ["key-three"]),
    ("secret=sec-one", ["sec-one"]),
    ("TOKEN=tok-delta", ["tok-delta"]),
    ("request failed with Bearer tok-epsilon, retrying", ["tok-epsilon"]),
    ('{"client_secret": "sec two", "token":"tok-zeta", "count": 3}', ["sec two", "tok-zeta"]),
    ("GET /v1/items?access_token=tok-eta&limit=5 HTTP/1.1", ["tok-eta"]),
    ("connect https://svc-user:pw-three@db.example.test:5432/main failed", ["pw-three"]),
    ("sent headers {Authorization: Basic cHctZm91cg==} to https://host.example.test", ["cHctZm91cg=="]),
    ("x-api-key: key-four", ["key-four"]),
    ("client_secret = 'sec three' next", ["sec three"]),
    ("nothing to hide here: count=3 name=widget", []),
]


@pytest.mark.parametrize("text, secrets", CASES)
def test_no_secret_value_survives(text, secrets):
    result = redact_text(text)
    for secret in secrets:
        assert secret not in result
    if secrets:
        assert PLACEHOLDER in result
    else:
        assert result == text


@pytest.mark.parametrize("text, secrets", CASES)
def test_redacting_twice_changes_nothing_more(text, secrets):
    once = redact_text(text)
    assert redact_text(once) == once


def test_labels_and_punctuation_are_kept():
    assert redact_text("password: pw-two") == "password: [REDACTED]"
    assert redact_text("api_key=key-one&page=2") == "api_key=[REDACTED]&page=2"
    assert redact_text('{"token":"tok-zeta", "count": 3}') == '{"token":"[REDACTED]", "count": 3}'
    assert redact_text("Authorization: Bearer tok-alpha") == "Authorization: Bearer [REDACTED]"
    assert redact_text("https://svc-user:pw-three@db.example.test/main") == "https://[REDACTED]@db.example.test/main"


def test_prose_that_only_mentions_the_words_is_left_alone():
    text = "the token was rejected and the password is wrong; secrets are stored elsewhere"
    assert redact_text(text) == text


def test_names_that_only_contain_a_credential_word_are_left_alone():
    text = "max_tokens=100 token_count: 5 tokens=3 secret_name=prod password_hint=none"
    assert redact_text(text) == text


def test_empty_and_plain_text():
    assert redact_text("") == ""
    assert redact_text("plain") == "plain"


def test_placeholder_can_be_changed():
    assert redact_text("secret=sec-one", placeholder="***") == "secret=***"
    assert redact_text("secret=sec-one", placeholder="\\1") == "secret=\\1"


def test_extra_patterns_may_be_plain_regexes_or_pairs():
    text = "ticket T-12345 for user id=77"
    assert redact_text(text, extra=[r"T-\d+"]) == "ticket [REDACTED] for user id=77"
    assert redact_text(text, extra=[(r"id=(\d+)", "id=#")]) == "ticket T-12345 for user id=#"
    assert redact_text(text, extra=[(re.compile(r"(?i)ticket (\S+)"), r"ticket <\1>")]) == "ticket <T-12345> for user id=77"


# --- against the copies --------------------------------------------------------------------------------------------


KEY_VALUE_COPY_CLEAN = [
    "password: pw-two", "password=pw-one", "api_key=key-one", "api-key: key-two", "secret=sec-one",
    "TOKEN=tok-delta", "token: tok-x and more text", "plain words only",
]


@pytest.mark.parametrize("text", KEY_VALUE_COPY_CLEAN)
def test_matches_the_key_value_copy_where_the_copy_is_right(text):
    assert redact_text(text) == copy_key_value(text)


def test_the_key_value_copy_left_the_bearer_value_in_an_authorization_header():
    text = "Authorization: Bearer tok-alpha"
    assert "tok-alpha" in copy_key_value(text)  # the copy redacts the word "Bearer" and leaks the token
    assert "tok-alpha" not in redact_text(text)


def test_the_key_value_copy_did_not_see_a_json_key_whose_closing_quote_comes_before_the_colon():
    text = '{"token": "tok-zeta"} done'
    assert copy_key_value(text) == text  # the copy changes nothing: the whole secret is left in
    assert redact_text(text) == '{"token": "[REDACTED]"} done'


BEARER_COPY_CLEAN = [
    "Authorization: Bearer tok-alpha access_token=tok-beta refresh-token: tok-gamma",
    "failed with Bearer tok-epsilon, retrying",
    "refresh token = tok-theta;",
    "plain words only",
]


@pytest.mark.parametrize("text", BEARER_COPY_CLEAN)
def test_removes_everything_the_bearer_copy_removed(text):
    copy_result = copy_bearer(text)
    result = redact_text(text)
    for secret in re.findall(r"tok-[a-z]+", text):  # the copy rewrites the separator to "="; the package keeps it
        assert secret not in copy_result
        assert secret not in result


def test_the_bearer_copy_missed_passwords_and_api_keys():
    text = "password=pw-one api_key=key-one"
    assert copy_bearer(text) == text
    assert "pw-one" not in redact_text(text) and "key-one" not in redact_text(text)


# --- keys and mappings -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key, expected",
    [("token", True), ("access_token", True), ("refreshToken", True), ("Authorization", True), ("client_secret", True),
     ("password", True), ("x-api-key", True), ("apiKey", True), ("private_key", True), ("Set-Cookie", True),
     ("count", False), ("component", False), ("operation", False), ("", False)],
)
def test_is_sensitive_key(key, expected):
    assert is_sensitive_key(key) is expected


def test_extra_words_extend_the_key_check_and_are_regexes():
    assert not is_sensitive_key("subject")
    assert is_sensitive_key("mail_subject", extra=("subject",))
    assert is_sensitive_key("Body", extra=("body|note",))
    assert is_sensitive_key("note", extra=("body|note",))
    assert "token" in SENSITIVE_WORDS


def test_masking_matches_the_payload_copy_when_given_that_copys_extra_words():
    payload = {
        "body": "private", "count": 2, "nested": {"url": "x", "ok": "fine", "deep": {"password": "pw"}},
        "rows": [{"email": "a", "n": 1}, "plain", 7], "name": "x" * 300, "token": "tok", "flag": True, "none": None,
    }
    extra = ("body", "subject", "url", "attachment", "note", "email")
    expected = copy_payload(payload)
    result = redact_mapping(payload, extra_keys=extra, max_string=256)
    assert result == expected
    assert payload["body"] == "private"  # the input is not changed


def test_dropping_matches_the_telemetry_key_rule():
    attributes = {"component": "sync", "retry_count": 2, "subject": "s", "authorization": "a", "operation": "x" * 400}
    extra = ("subject", "body", "attachment", "note", "url", "email")
    kept = {key: value for key, value in attributes.items() if not COPY_TELEMETRY_KEYS.search(key)}
    result = redact_mapping(attributes, extra_keys=extra, mode="drop", max_string=256)
    assert set(result) == set(kept) == {"component", "retry_count", "operation"}
    assert result["operation"] == "x" * 256


def test_mapping_handles_tuples_scalars_and_text_scrubbing():
    assert redact_mapping({"items": ({"token": "t"}, 5)}) == {"items": [{"token": PLACEHOLDER}, 5]}
    assert redact_mapping(5) == 5
    assert redact_mapping("secret=abc") == "secret=abc"
    assert redact_mapping({"msg": "secret=abc"}, scrub_text=True) == {"msg": "secret=[REDACTED]"}
    assert redact_mapping({"token": "t"}, placeholder="***") == {"token": "***"}


def test_mode_is_checked():
    with pytest.raises(ValueError):
        redact_mapping({}, mode="hide")
