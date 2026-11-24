"""Retry policy, `Retry-After` parsing, `retry_call` and `poll`. Nothing here imports an HTTP library."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable, Optional, Tuple, TypeVar

T = TypeVar("T")

DEFAULT_STATUSES = frozenset({429, 500, 502, 503, 504})


def parse_retry_after(value: Any, now: Optional[Callable[[], float]] = None) -> Optional[float]:
    """Seconds to wait from a `Retry-After` header value, or None when it is missing or unusable.

    Accepts a number of seconds (`"120"`, `"1.5"`) or an HTTP date. A date in the past gives 0.
    Negative, infinite, empty and unparseable values give None, so the caller falls back to backoff.
    """
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("latin-1")
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            moment = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        seconds = moment.timestamp() - (now or time.time)()
        return max(seconds, 0.0)
    if seconds != seconds or seconds < 0 or seconds == float("inf"):
        return None
    return seconds


def status_of(outcome: Any) -> Optional[int]:
    """The HTTP status of a response or an HTTP error: `status_code` (requests, httpx), `status` (http.client,
    aiohttp) or `code` (urllib's `HTTPError`). None for anything else, such as a `URLError` or a timeout."""
    for name in ("status_code", "status", "code"):
        value = getattr(outcome, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _header(headers: Any, name: str) -> Any:
    if headers is None:
        return None
    try:
        value = headers.get(name)
        if value is not None:
            return value
        items = headers.items()
    except (AttributeError, TypeError):
        return None
    wanted = name.lower()
    for key, value in items:
        if str(key).lower() == wanted:
            return value
    return None


@dataclass(frozen=True)
class RetryPolicy:
    """When to retry and how long to wait.

    max_attempts: total tries, including the first. 1 never retries.
    statuses: HTTP statuses worth another try.
    base_delay, factor: the wait before retry n (counting from 0) is `base_delay * factor ** n`.
    max_delay: ceiling on any wait, `Retry-After` included. None means no ceiling.
    min_delay: floor on any wait.
    default_retry_after: the wait to use on a retryable status that sent no usable `Retry-After`,
        instead of the backoff. None keeps the backoff.
    jitter: add a random share of the wait, up to `jitter * wait`. 0 keeps waits exact.
    """

    max_attempts: int = 4
    statuses: frozenset = DEFAULT_STATUSES
    base_delay: float = 1.0
    factor: float = 2.0
    max_delay: Optional[float] = 30.0
    min_delay: float = 0.0
    default_retry_after: Optional[float] = None
    jitter: float = 0.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay < 0 or self.min_delay < 0 or self.jitter < 0 or self.factor < 1:
            raise ValueError("delays and jitter must not be negative, and factor must be at least 1")
        if self.max_delay is not None and self.max_delay < self.min_delay:
            raise ValueError("max_delay must not be below min_delay")
        object.__setattr__(self, "statuses", frozenset(self.statuses))

    def delay(self, retry: int, retry_after: Any = None, rng: Callable[[], float] = random.random) -> float:
        """Seconds to wait before retry number `retry` (0 for the first retry).

        `retry_after` is a raw `Retry-After` header value. A usable one beats the backoff.
        """
        wanted = parse_retry_after(retry_after)
        if wanted is None:
            wanted = self.default_retry_after
        if wanted is None:
            try:
                wanted = self.base_delay * self.factor ** retry
            except OverflowError:
                wanted = float("inf")
        if self.jitter:
            wanted += wanted * self.jitter * rng()
        wanted = max(wanted, self.min_delay)
        if self.max_delay is not None:
            wanted = min(wanted, self.max_delay)
        return wanted


@dataclass(frozen=True)
class RetryInfo:
    """What `retry_call` tells `on_retry` before it sleeps."""

    attempt: int  # the try that just failed, counting from 1
    status: Optional[int]
    delay: float
    outcome: Any  # the response or the exception


def retry_call(
    send: Callable[[], T],
    policy: Optional[RetryPolicy] = None,
    *,
    sleep: Optional[Callable[[float], None]] = None,
    on_retry: Optional[Callable[[RetryInfo], None]] = None,
    retry_exceptions: Tuple[type, ...] = (),
    retry_if: Optional[Callable[[Any], Optional[bool]]] = None,
) -> T:
    """Call `send()` until it gives an answer worth keeping.

    A response (any object with a status) is retried when its status is in `policy.statuses`.
    An exception that carries a status, such as urllib's `HTTPError`, is judged the same way.
    Any other exception is raised at once unless it is an instance of `retry_exceptions`.
    `retry_if(outcome)` runs first and may say True (retry), False (stop) or None (use the rules above).
    The wait comes from the outcome's `Retry-After` header when it has one, else from the policy.

    When the tries run out, the last response is returned (so the caller still sees the 429 or 503)
    and the last exception is raised.
    """
    policy = policy or RetryPolicy()
    sleep = sleep or time.sleep  # looked up now, so a test can patch time.sleep
    attempt = 0
    while True:
        attempt += 1
        error: Optional[BaseException] = None
        try:
            outcome = send()
        except BaseException as exc:  # noqa: BLE001 - re-raised below unless it is retryable
            if not isinstance(exc, Exception):
                raise
            outcome = error = exc
        status = status_of(outcome)
        verdict = retry_if(outcome) if retry_if else None
        if verdict is None:
            if error is not None and status is None:
                verdict = isinstance(error, retry_exceptions)
            else:
                verdict = status in policy.statuses
        if not verdict or attempt >= policy.max_attempts:
            if error is not None:
                raise error
            return outcome  # type: ignore[return-value]
        wait = policy.delay(attempt - 1, _header(getattr(outcome, "headers", None), "Retry-After"))
        if on_retry:
            on_retry(RetryInfo(attempt, status, wait, outcome))
        sleep(wait)


class PollTimeout(TimeoutError):
    """`poll` used up its tries without `done` saying yes. `last` holds the last value fetched."""

    def __init__(self, polls: int, last: Any = None) -> None:
        super().__init__(f"not finished after {polls} polls")
        self.polls = polls
        self.last = last


def poll(
    fetch: Callable[[], T],
    done: Callable[[T], bool],
    *,
    interval: float = 5.0,
    max_polls: int = 60,
    sleep: Optional[Callable[[float], None]] = None,
) -> T:
    """Call `fetch()` until `done(value)` is true and return that value.

    It waits `interval` seconds between calls and raises `PollTimeout` after `max_polls` calls.
    Wrap `fetch` in `retry_call` to ride out 429 and 5xx answers between polls. An exception from
    `fetch` or `done` is not caught.
    """
    if max_polls < 1:
        raise ValueError("max_polls must be at least 1")
    sleep = sleep or time.sleep
    value: Any = None
    for count in range(1, max_polls + 1):
        value = fetch()
        if done(value):
            return value
        if count < max_polls:
            sleep(interval)
    raise PollTimeout(max_polls, value)
