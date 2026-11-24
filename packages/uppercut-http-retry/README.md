# uppercut-http-retry

Retries an HTTP call that fails with 429 or a 5xx status, waiting as long as the server's `Retry-After` header says or, without one, an exponential backoff. It also polls an asynchronous operation (the `Operation-Location` pattern) until it finishes. It does not import an HTTP library: it works on whatever your client returns or raises, so it fits `urllib`, `requests`, `httpx` and `http.client` alike. Standard library only.

```sh
pip install uppercut-http-retry
```

## Retry a request

```python
import json
import urllib.request

from uppercut_http_retry import RetryPolicy, retry_call

request = urllib.request.Request("https://api.example.test/v1/items", headers={"Accept": "application/json"})

# urllib raises HTTPError on 429/5xx; retry_call reads its status and Retry-After header.
with retry_call(lambda: urllib.request.urlopen(request, timeout=30),
                RetryPolicy(max_attempts=4, max_delay=30)) as reply:
    items = json.load(reply)
```

With `requests` (or `httpx`) the 429 comes back as a response, not an exception. `retry_call` retries it and, when the tries run out, hands the last response back so you can look at the status:

```python
import requests
from uppercut_http_retry import RetryPolicy, retry_call

analyze = RetryPolicy(max_attempts=2, statuses={429}, default_retry_after=60, min_delay=5, max_delay=None)
reply = retry_call(lambda: requests.post(url, json=payload, timeout=120), analyze)
if reply.status_code != 202:
    raise RuntimeError(reply.text[:500])
```

## Poll an operation

```python
from uppercut_http_retry import RetryPolicy, poll, retry_call

policy = RetryPolicy(max_attempts=5, statuses={429}, default_retry_after=60, min_delay=5, max_delay=None)

def fetch():
    return retry_call(lambda: requests.get(operation_url, timeout=60), policy)  # rides out 429 between polls

result = poll(fetch, lambda r: r.json().get("status") in {"succeeded", "failed"}, interval=5, max_polls=60)
```

`poll` raises `PollTimeout` (a `TimeoutError`; `.last` holds the last value) when `max_polls` calls were not enough.

## API

| Name | What it does |
| --- | --- |
| `RetryPolicy(max_attempts=4, statuses={429, 500, 502, 503, 504}, base_delay=1.0, factor=2.0, max_delay=30.0, min_delay=0.0, default_retry_after=None, jitter=0.0)` | When to retry and how long to wait. `policy.delay(retry, retry_after=None)` gives the wait before a retry, counting from 0 |
| `retry_call(send, policy=None, *, sleep=None, on_retry=None, retry_exceptions=(), retry_if=None)` | Call `send()` until it returns something that is not worth retrying. Returns the last response, or raises the last exception, when the tries run out |
| `poll(fetch, done, *, interval=5.0, max_polls=60, sleep=None)` | Call `fetch()` until `done(value)` is true. Raises `PollTimeout` |
| `parse_retry_after(value)` | Seconds from a `Retry-After` value: a number or an HTTP date. `None` when missing, negative or unreadable |
| `status_of(outcome)` | The HTTP status of a response or `HTTPError` (`status_code`, `status` or `code`), else `None` |
| `sleep` (both functions) | `None` means `time.sleep`, looked up on each call. Pass your own to test without waiting |
| `RetryInfo(attempt, status, delay, outcome)` | What `on_retry` receives before each wait, for logging or a metric |

## How waits are chosen

- A usable `Retry-After` wins. Otherwise `default_retry_after` is used when the policy has one, otherwise the backoff `base_delay * factor ** n` for retry `n` (1, 2, 4, 8 seconds by default).
- The result is raised to `min_delay` and cut to `max_delay`. Pass `max_delay=None` to trust the server's `Retry-After` however long it is. The default ceiling is 30 seconds.
- `jitter` adds up to that share of the wait (0.2 means up to 20% more) so many clients do not retry together. It is 0 by default so waits are exact.
- `Retry-After` as an HTTP date is converted to seconds from now; a date in the past means no wait.

## What is retried

- A response or an `HTTPError` whose status is in `policy.statuses`.
- An exception that is an instance of `retry_exceptions`, for example `(TimeoutError, ConnectionError)`. Any other exception, including `URLError` and `KeyboardInterrupt`, is raised at once.
- Whatever `retry_if(outcome)` says `True` for, and never what it says `False` for. Return `None` to leave the decision to the rules above. Use it to retry on a body, such as a provider's own "overloaded" error.
- Only idempotent calls are safe to retry. Retrying a POST can repeat its effect; the package does not know which calls are safe.

## License

MIT
