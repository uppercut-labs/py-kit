"""Pattern-based scrubbing of text and of mappings. Standard library only."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Iterable, Optional, Pattern, Tuple, Union

PLACEHOLDER = "[REDACTED]"

# Words that mark a key or a label as holding a credential. Matched anywhere in a name, ignoring case.
SENSITIVE_WORDS: Tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "passwd",
    "passphrase",
    "authorization",
    "api[_-]?key",
    "credential",
    "cookie",
    "private[_-]?key",
)

_CREDENTIAL = r"token|secret|passw(?:or)?d|passphrase|api[_-]?key|credential|private[_-]?key"
_NAME_CHAR = r"[A-Za-z0-9_.-]"

# scheme://user:password@host
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]*:[^/\s@]+@")
# Authorization: Bearer <value>, Authorization=<value>, "authorization": "Basic <value>"
_AUTHORIZATION = re.compile(
    r"""(?ix)
    (?<![A-Za-z0-9_.-])
    ((?:proxy-)?authorization)
    (["']?\s*[:=]\s*["']?)
    ((?:bearer|basic|digest|token)\s+)?
    ([^\s,;&"'})]+)
    """
)
# a bearer credential on its own: "failed with Bearer <value>"
_BEARER = re.compile(r"(?i)\b(bearer\s+)[^\s,;]+")
# access_token=<value>, "client_secret": "<value>", ?api-key=<value>, password: <value>
_KEY_VALUE = re.compile(
    rf"""(?ix)
    (?<!{_NAME_CHAR})
    ({_NAME_CHAR}*(?:{_CREDENTIAL}){_NAME_CHAR}*)
    (["']?\s*[:=]\s*)
    ("[^"]*"|'[^']*'|[^\s,;&"'}})]+)
    """
)


def _extra_patterns(extra: Iterable[Union[str, Pattern[str], Tuple[Union[str, Pattern[str]], str]]]):
    for item in extra:
        if isinstance(item, tuple):
            pattern, replacement = item
        else:
            pattern, replacement = item, None
        yield re.compile(pattern) if isinstance(pattern, str) else pattern, replacement


def redact_text(text: str, *, placeholder: str = PLACEHOLDER, extra: Iterable[Any] = ()) -> str:
    """Replace credential-shaped values in `text` with `placeholder`, keeping the label that names them.

    Handled: a password inside a URL, `Authorization` headers (the scheme stays: `Authorization: Bearer [REDACTED]`),
    a bare `Bearer <value>`, and `name=value`, `name: value` and `"name": "value"` where the name contains
    token, secret, password, passwd, passphrase, api key, credential or private key (so `access_token`,
    `client_secret` and `x-api-key` are all caught). A quoted value keeps its quotes, and an unquoted one
    ends at whitespace, a comma, a semicolon, `&` or a closing bracket. Cookie headers are not text-scrubbed
    (they hold several values); `cookie` is a sensitive key name for `redact_mapping`.

    `extra` adds patterns: a regex (the whole match is replaced) or a `(regex, replacement)` pair, where the
    replacement may use group references such as `\\1`. Running it twice changes nothing more.
    """
    if not text:
        return text
    mask = placeholder.replace("\\", "\\\\")
    out = _URL_USERINFO.sub(lambda m: f"{m.group(1)}{placeholder}@", text)

    def authorization(match: "re.Match[str]") -> str:
        return f"{match.group(1)}{match.group(2)}{match.group(3) or ''}{placeholder}"

    out = _AUTHORIZATION.sub(authorization, out)
    out = _BEARER.sub(lambda m: f"{m.group(1)}{placeholder}", out)

    def key_value(match: "re.Match[str]") -> str:
        value = match.group(3)
        quote = value[0] if value[0] in "\"'" and len(value) > 1 else ""
        return f"{match.group(1)}{match.group(2)}{quote}{placeholder}{quote}"

    out = _KEY_VALUE.sub(key_value, out)
    for pattern, replacement in _extra_patterns(extra):
        out = pattern.sub(mask if replacement is None else replacement, out)
    return out


@lru_cache(maxsize=32)
def _key_pattern(extra: Tuple[str, ...]) -> Pattern[str]:
    return re.compile("(?i)(" + "|".join(SENSITIVE_WORDS + extra) + ")")


def is_sensitive_key(key: object, extra: Iterable[str] = ()) -> bool:
    """True when a key name contains a credential word (`SENSITIVE_WORDS`) or one of the `extra` words.

    `extra` entries are regular expressions, matched anywhere in the name ignoring case:
    `is_sensitive_key("mail_subject", extra=("subject",))`.
    """
    return bool(_key_pattern(tuple(extra)).search(str(key)))


def redact_mapping(
    value: Any,
    *,
    extra_keys: Iterable[str] = (),
    mode: str = "mask",
    placeholder: str = PLACEHOLDER,
    max_string: Optional[int] = None,
    scrub_text: bool = False,
) -> Any:
    """A copy of nested dicts and lists with the values of sensitive keys masked or dropped.

    mode: `"mask"` replaces a sensitive key's value with `placeholder`; `"drop"` removes the key.
    extra_keys: more words that make a key sensitive, as in `is_sensitive_key`.
    max_string: cut every string value to this many characters.
    scrub_text: also run `redact_text` over string values whose keys are not sensitive.
    The input is not changed. Tuples come back as lists; other values are returned as they are.
    """
    if mode not in ("mask", "drop"):
        raise ValueError('mode must be "mask" or "drop"')
    extra = tuple(extra_keys)

    def walk(item: Any) -> Any:
        if isinstance(item, dict):
            result = {}
            for key, inner in item.items():
                if is_sensitive_key(key, extra):
                    if mode == "mask":
                        result[key] = placeholder
                    continue
                result[key] = walk(inner)
            return result
        if isinstance(item, (list, tuple)):
            return [walk(inner) for inner in item]
        if isinstance(item, str):
            if scrub_text:
                item = redact_text(item, placeholder=placeholder)
            return item[:max_string] if max_string is not None else item
        return item

    return walk(value)
