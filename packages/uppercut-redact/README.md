# uppercut-redact

Scrubs credential-shaped text before it reaches a log, an error message or a status line, and masks or drops sensitive keys in a dictionary before it is written as telemetry or an audit record. It finds bearer tokens, `Authorization` headers, `name=value` and `"name": "value"` pairs whose name holds a credential word, and passwords inside URLs, and it keeps the label so the line still reads. Standard library only.

```sh
pip install uppercut-redact
```

```python
from uppercut_redact import redact_mapping, redact_text

redact_text("GET /v1/items?access_token=abc123&limit=5 failed: Authorization: Bearer abc123")
# 'GET /v1/items?access_token=[REDACTED]&limit=5 failed: Authorization: Bearer [REDACTED]'

redact_text('{"client_secret": "x y", "count": 3}')
# '{"client_secret": "[REDACTED]", "count": 3}'

# Keys: mask a sensitive key's value, or drop the key, and cut long strings.
event = {"component": "sync", "token": "abc123", "subject": "hello", "detail": "x" * 400}
redact_mapping(event, extra_keys=("subject",), max_string=256)
# {'component': 'sync', 'token': '[REDACTED]', 'subject': '[REDACTED]', 'detail': 'xxx...' (256 characters)}
redact_mapping(event, extra_keys=("subject",), mode="drop")
# {'component': 'sync', 'detail': 'xxx...'}
```

## API

| Name | What it does |
| --- | --- |
| `redact_text(text, *, placeholder="[REDACTED]", extra=())` | Replace credential-shaped values in a string. `extra` takes more regexes (the whole match is replaced) or `(regex, replacement)` pairs |
| `redact_mapping(value, *, extra_keys=(), mode="mask", placeholder="[REDACTED]", max_string=None, scrub_text=False)` | A copy of nested dicts and lists. `mode="mask"` replaces a sensitive key's value; `mode="drop"` removes the key; `max_string` cuts strings; `scrub_text=True` also runs `redact_text` on the other strings |
| `is_sensitive_key(key, extra=())` | True when a key name contains a credential word or one of the `extra` regexes |
| `SENSITIVE_WORDS`, `PLACEHOLDER` | The credential words (token, secret, password, passwd, passphrase, authorization, api key, credential, cookie, private key) and the default replacement |

## What `redact_text` catches

- `Authorization: Bearer <value>`, `Authorization=<value>`, `"Authorization": "Basic <value>"` (the scheme stays), also `Proxy-Authorization`.
- A bare `Bearer <value>` anywhere in the text.
- `name=value`, `name: value`, `name = 'value'` and `"name": "value"` where the name contains token, secret, password, passwd, passphrase, api key, credential or private key, in any case and with any prefix or suffix (`access_token`, `client_secret`, `x-api-key`, `apiKey`). A quoted value keeps its quotes; an unquoted value ends at whitespace, a comma, a semicolon, `&` or a closing bracket, so a query string keeps its other parameters.
- `scheme://user:password@host`: the user and password are replaced.
- Running it twice gives the same result.

## What it does not do

- It matches shapes, not values: a secret that appears alone, with no label (a bare API key pasted into a message), is not found. Add a pattern for a key format you know with `extra`.
- Words in ordinary sentences are left alone (`the token was rejected`), but `bearer` followed by any word is treated as a bearer credential.
- A `Cookie` header holds several values, so it is not text-scrubbed; `cookie` is a sensitive key name for `redact_mapping`.
- It is a last line of defence for diagnostics. Keep secrets out of messages in the first place.

## License

MIT
