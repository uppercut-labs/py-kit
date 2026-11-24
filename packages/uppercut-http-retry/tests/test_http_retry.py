"""Behaviour of uppercut-http-retry, including a check against the two hand-written loops it replaces.

The loops under "copies" are the retry logic of two real callers, reduced to what decides the waits and
rewritten against urllib and requests-style objects. Both run beside the package on the same scripted
answers, and the sleeps they make must be equal.
"""

import datetime
import email.utils
import io
import urllib.error
from email.message import Message
from types import SimpleNamespace

import pytest

from uppercut_http_retry import (
    DEFAULT_STATUSES,
    PollTimeout,
    RetryInfo,
    RetryPolicy,
    parse_retry_after,
    poll,
    retry_call,
    status_of,
)


class Recorder:
    def __init__(self):
        self.sleeps = []

    def __call__(self, seconds):
        self.sleeps.append(seconds)


def http_error(code, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://api.invalid/x", code, "synthetic", headers, io.BytesIO(b""))


def response(status, retry_after=None):
    headers = {} if retry_after is None else {"retry-after": retry_after}
    return SimpleNamespace(status_code=status, headers=headers)


def scripted(*steps):
    """A `send` that plays each step: an exception is raised, anything else is returned."""
    queue = list(steps)

    def send():
        step = queue.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    send.left = lambda: len(queue)
    return send


# --- parse_retry_after -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [("0", 0.0), ("7", 7.0), (" 12 ", 12.0), ("1.5", 1.5), (b"3", 3.0), (4, 4.0)],
)
def test_parse_seconds(value, expected):
    assert parse_retry_after(value) == expected


@pytest.mark.parametrize("value", [None, "", "  ", "soon", "-5", "nan", "inf", "Mon, 99 Foo 2026 25:61:61 GMT"])
def test_parse_rejects_unusable_values(value):
    assert parse_retry_after(value) is None


def test_parse_http_date_relative_to_now():
    now = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    later = email.utils.format_datetime(now + datetime.timedelta(seconds=90), usegmt=True)
    earlier = email.utils.format_datetime(now - datetime.timedelta(seconds=90), usegmt=True)
    assert parse_retry_after(later, now=lambda: now.timestamp()) == 90.0
    assert parse_retry_after(earlier, now=lambda: now.timestamp()) == 0.0


# --- status_of -------------------------------------------------------------------------------------------


def test_status_of_reads_each_library_shape():
    assert status_of(SimpleNamespace(status_code=429)) == 429
    assert status_of(SimpleNamespace(status=503)) == 503
    assert status_of(http_error(502)) == 502
    assert status_of(urllib.error.URLError("down")) is None
    assert status_of(SimpleNamespace(status=True)) is None
    assert status_of(object()) is None


# --- RetryPolicy -----------------------------------------------------------------------------------------


def test_default_backoff_is_one_two_four_capped_at_thirty():
    policy = RetryPolicy()
    assert [policy.delay(n) for n in range(8)] == [1, 2, 4, 8, 16, 30, 30, 30]


def test_retry_after_beats_backoff_and_is_capped_by_max_delay():
    policy = RetryPolicy()
    assert policy.delay(0, "3") == 3
    assert policy.delay(0, "300") == 30
    assert policy.delay(2, "garbage") == 4


def test_default_retry_after_replaces_backoff_only_when_no_header_is_usable():
    policy = RetryPolicy(default_retry_after=60, min_delay=5, max_delay=None)
    assert policy.delay(0) == 60
    assert policy.delay(3, "nope") == 60
    assert policy.delay(0, "2") == 5
    assert policy.delay(0, "600") == 600


def test_jitter_adds_a_share_of_the_wait_and_never_goes_over_the_ceiling():
    policy = RetryPolicy(jitter=0.5, max_delay=10)
    assert policy.delay(1, rng=lambda: 0.0) == 2
    assert policy.delay(1, rng=lambda: 1.0) == 3
    assert policy.delay(3, rng=lambda: 1.0) == 10


def test_huge_retry_counts_do_not_overflow():
    assert RetryPolicy(max_delay=30).delay(5000) == 30
    assert RetryPolicy(max_delay=None).delay(5000) == float("inf")


@pytest.mark.parametrize(
    "kwargs",
    [{"max_attempts": 0}, {"base_delay": -1}, {"min_delay": -1}, {"jitter": -0.1}, {"factor": 0.5},
     {"max_delay": 1, "min_delay": 2}],
)
def test_bad_policies_are_refused(kwargs):
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


def test_statuses_accept_any_iterable():
    assert RetryPolicy(statuses=[429]).statuses == frozenset({429})
    assert DEFAULT_STATUSES == {429, 500, 502, 503, 504}


# --- retry_call ------------------------------------------------------------------------------------------


def test_success_on_the_first_try_never_sleeps():
    sleep = Recorder()
    ok = response(200)
    assert retry_call(scripted(ok), sleep=sleep) is ok
    assert sleep.sleeps == []


def test_retries_a_returned_429_then_returns_the_good_response():
    sleep = Recorder()
    ok = response(200)
    assert retry_call(scripted(response(429, "4"), response(503), ok), sleep=sleep) is ok
    assert sleep.sleeps == [4.0, 2.0]


def test_other_statuses_are_returned_at_once():
    sleep = Recorder()
    bad = response(404)
    assert retry_call(scripted(bad), sleep=sleep) is bad
    assert sleep.sleeps == []


def test_running_out_of_tries_returns_the_last_response():
    sleep = Recorder()
    last = response(503)
    result = retry_call(scripted(response(503), response(503), last), RetryPolicy(max_attempts=3), sleep=sleep)
    assert result is last
    assert sleep.sleeps == [1, 2]


def test_running_out_of_tries_raises_the_last_http_error():
    sleep = Recorder()
    last = http_error(500)
    with pytest.raises(urllib.error.HTTPError) as caught:
        retry_call(scripted(http_error(500), last), RetryPolicy(max_attempts=2), sleep=sleep)
    assert caught.value is last
    assert sleep.sleeps == [1]


def test_non_http_exceptions_are_raised_without_retrying():
    sleep = Recorder()
    with pytest.raises(urllib.error.URLError):
        retry_call(scripted(urllib.error.URLError("refused")), sleep=sleep)
    assert sleep.sleeps == []


def test_http_errors_with_other_statuses_are_raised_without_retrying():
    sleep = Recorder()
    with pytest.raises(urllib.error.HTTPError):
        retry_call(scripted(http_error(404)), sleep=sleep)
    assert sleep.sleeps == []


def test_retry_exceptions_are_retried():
    sleep = Recorder()
    ok = response(200)
    result = retry_call(scripted(TimeoutError("slow"), ConnectionError("reset"), ok), sleep=sleep,
                        retry_exceptions=(TimeoutError, ConnectionError))
    assert result is ok
    assert sleep.sleeps == [1, 2]


def test_keyboard_interrupt_is_never_swallowed():
    with pytest.raises(KeyboardInterrupt):
        retry_call(scripted(KeyboardInterrupt()), sleep=Recorder(), retry_exceptions=(BaseException,))


def test_retry_if_can_force_or_stop_a_retry():
    sleep = Recorder()
    soft = SimpleNamespace(status_code=200, body="try again")
    ok = SimpleNamespace(status_code=200, body="done")
    result = retry_call(scripted(soft, ok), sleep=sleep, retry_if=lambda r: True if r.body == "try again" else None)
    assert result is ok and sleep.sleeps == [1]
    stop = response(503)
    assert retry_call(scripted(stop), sleep=sleep, retry_if=lambda r: False) is stop
    assert sleep.sleeps == [1]


def test_on_retry_sees_attempt_status_delay_and_outcome():
    seen = []
    first = response(429, "2")
    retry_call(scripted(first, response(200)), sleep=Recorder(), on_retry=seen.append)
    assert seen == [RetryInfo(attempt=1, status=429, delay=2.0, outcome=first)]


def test_header_lookup_ignores_case_and_accepts_mappings_message_and_none():
    sleep = Recorder()
    odd = SimpleNamespace(status_code=429, headers={"RETRY-AFTER": "9"})
    retry_call(scripted(odd, response(200)), sleep=sleep)
    nothing = SimpleNamespace(status_code=429, headers=None)
    retry_call(scripted(nothing, response(200)), sleep=sleep)
    retry_call(scripted(http_error(429, "6"), response(200)), sleep=sleep)
    assert sleep.sleeps == [9.0, 1.0, 6.0]


def test_sleep_defaults_to_time_sleep_looked_up_at_call_time(monkeypatch):
    import time

    seen = []
    monkeypatch.setattr(time, "sleep", seen.append)
    retry_call(scripted(response(503), response(200)))
    poll(scripted(1, 2).__call__, lambda v: v == 2, interval=3)
    assert seen == [1, 3]


# --- poll ------------------------------------------------------------------------------------------------


def test_poll_returns_the_first_finished_value():
    sleep = Recorder()
    values = iter(["running", "running", "succeeded"])
    assert poll(lambda: next(values), lambda v: v == "succeeded", interval=5, sleep=sleep) == "succeeded"
    assert sleep.sleeps == [5, 5]


def test_poll_times_out_after_max_polls_and_keeps_the_last_value():
    sleep = Recorder()
    calls = []

    def fetch():
        calls.append(1)
        return "running"

    with pytest.raises(PollTimeout) as caught:
        poll(fetch, lambda v: False, interval=5, max_polls=4, sleep=sleep)
    assert len(calls) == 4 and sleep.sleeps == [5, 5, 5]
    assert caught.value.polls == 4 and caught.value.last == "running"
    assert isinstance(caught.value, TimeoutError)


def test_poll_needs_at_least_one_poll():
    with pytest.raises(ValueError):
        poll(lambda: 1, lambda v: True, max_polls=0)


def test_poll_and_retry_call_work_together():
    sleep = Recorder()
    answers = scripted(response(200), response(429, "3"), SimpleNamespace(status_code=200, state="done"))

    def fetch():
        return retry_call(answers, sleep=sleep)

    result = poll(fetch, lambda r: getattr(r, "state", "") == "done", interval=5, sleep=sleep)
    assert result.state == "done"
    assert sleep.sleeps == [5, 3.0]


# --- the copies this package replaces -----------------------------------------------------------------------
#
# Graph client (urllib): four tries on 429/500/502/503/504, wait = Retry-After as a float, else 2**attempt,
# at most 30 seconds, a Retry-After that is not a number falls back to 2**attempt.


def graph_copy(steps):
    sleeps, queue = [], list(steps)

    def opener():
        step = queue.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    for attempt in range(4):
        try:
            return opener(), sleeps
        except urllib.error.HTTPError as error:
            if error.code in {429, 500, 502, 503, 504} and attempt < 3:
                retry_after = error.headers.get("Retry-After") if error.headers else None
                try:
                    delay = min(float(retry_after or 2**attempt), 30.0)
                except ValueError:
                    delay = float(2**attempt)
                sleeps.append(delay)
                continue
            return error, sleeps
    raise AssertionError("unreachable")


GRAPH_SCRIPTS = [
    [200],
    [429, 200],
    [(429, "5"), 200],
    [(429, "0"), (503, "1.5"), (500, None), "ok"],
    [(429, "500"), (502, None), (504, "soon"), "ok"],
    [(503, None), (503, None), (503, None), (503, None)],
    [(500, None), 404],
    [404],
    [(429, "17"), (429, "29.9"), (429, "31"), (429, "1")],
]


def build_steps(script):
    steps = []
    for item in script:
        if item == "ok" or item == 200:
            steps.append(response(200))
        elif isinstance(item, tuple):
            steps.append(http_error(*item))
        else:
            steps.append(http_error(item))
    return steps


@pytest.mark.parametrize("script", GRAPH_SCRIPTS)
def test_matches_the_graph_client_loop(script):
    expected_result, expected_sleeps = graph_copy(build_steps(script))
    sleep = Recorder()
    send = scripted(*build_steps(script))
    try:
        result = retry_call(send, RetryPolicy(max_attempts=4, max_delay=30.0), sleep=sleep)
    except urllib.error.HTTPError as error:
        result = error
    assert sleep.sleeps == expected_sleeps
    assert status_of(result) == status_of(expected_result)


# Document Intelligence calls (requests): a 429 waits Retry-After seconds (60 when absent, at least 5, no cap)
# and the POST is tried a second time; the status poll sleeps the same way and counts a 429 as a poll.


def azs_post_copy(statuses_with_headers):
    sleeps, queue = [], list(statuses_with_headers)
    first = queue.pop(0)
    if first[0] == 429:
        sleeps.append(max(int(first[1] or "60"), 5))
        second = queue.pop(0)
        return second[0], sleeps
    return first[0], sleeps


AZS_POLICY = RetryPolicy(max_attempts=2, statuses={429}, default_retry_after=60, min_delay=5, max_delay=None)


@pytest.mark.parametrize(
    "script",
    [[(202, None)], [(429, None), (202, None)], [(429, "2"), (202, None)], [(429, "120"), (429, "1")],
     [(500, None)], [(429, "7"), (200, None)]],
)
def test_matches_the_document_intelligence_post_loop(script):
    expected_status, expected_sleeps = azs_post_copy(script)
    sleep = Recorder()
    result = retry_call(scripted(*[response(s, h) for s, h in script]), AZS_POLICY, sleep=sleep)
    assert result.status_code == expected_status
    assert sleep.sleeps == expected_sleeps


def test_document_intelligence_poll_waits_the_same_between_429_and_running_answers():
    # copy: 429 -> sleep(max(Retry-After or 60, 5)); running -> sleep(5); succeeded -> return
    script = [(429, "9"), "running", (429, None), "running", "succeeded"]
    expected = [9, 5, 60, 5]
    sleep = Recorder()
    queue = []
    for item in script:
        if isinstance(item, tuple):
            queue.append(response(*item))
        else:
            queue.append(SimpleNamespace(status_code=200, state=item, headers={}))
    answers = scripted(*queue)
    policy = RetryPolicy(max_attempts=5, statuses={429}, default_retry_after=60, min_delay=5, max_delay=None)
    result = poll(lambda: retry_call(answers, policy, sleep=sleep), lambda r: r.state == "succeeded",
                  interval=5, max_polls=60, sleep=sleep)
    assert result.state == "succeeded"
    assert sleep.sleeps == expected
