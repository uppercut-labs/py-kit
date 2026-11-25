import json
from collections import namedtuple

import httpx
import pytest

from uppercut_grounded_answers import (
    ANSWER_JSON_SCHEMA,
    ChatCompletionsProvider,
    FailoverRouter,
    GeneratedAnswer,
    GroundingFailure,
    OllamaChatProvider,
    ProviderFailure,
    decode_answer,
    embedded_json_objects,
    http_failure,
    status_failure,
)

Evidence = namedtuple("Evidence", "chunk_id text")
EVIDENCE = (Evidence("c-1", "first excerpt"), Evidence("c-2", "second excerpt"))
IDS = {"c-1", "c-2"}
OK = GeneratedAnswer("ok", "Grounded.", ("c-1",))


def reply(status="ok", answer="Grounded.", citations=("c-1",)):
    return json.dumps({"status": status, "answer": answer, "citations": list(citations)})


# decode_answer ------------------------------------------------------------------------------------------

def test_decodes_a_plain_answer():
    got = decode_answer("p", reply(citations=("c-2", "c-1", "c-2")), IDS)
    assert got == GeneratedAnswer("ok", "Grounded.", ("c-2", "c-1"))  # order kept, repeats dropped


def test_decodes_a_code_fence():
    assert decode_answer("p", "```json\n" + reply() + "\n```", IDS).answer == "Grounded."
    assert decode_answer("p", "```\n" + reply() + "\n```", IDS).answer == "Grounded."


def test_the_last_answer_object_wins_over_an_earlier_draft():
    text = "<think>" + reply(answer="Draft.") + "</think>\n```json\n" + reply(answer="Final.") + "\n```"
    got = decode_answer("p", text, IDS)
    assert got.answer == "Final." and got.cited_chunk_ids == ("c-1",)


def test_an_answer_object_beats_a_later_unrelated_object():
    text = "Result: " + reply(answer="Real.") + ' and then {"note": "unrelated"}'
    assert decode_answer("p", text, IDS).answer == "Real."


def test_abstention_is_an_answer_not_a_failure():
    got = decode_answer("p", reply("insufficient_evidence", "Not enough information.", ()), IDS)
    assert got.status == "insufficient_evidence" and got.cited_chunk_ids == ()


@pytest.mark.parametrize("content", [None, "", "   ", 5, [], "no json at all", "[]", '"text"', "{not json",
                                      '{"status": "ok"}', '{"status": "maybe", "answer": "a", "citations": []}',
                                      '{"status": "ok", "answer": 3, "citations": []}',
                                      '{"status": "ok", "answer": "a", "citations": "c-1"}'])
def test_malformed_replies_are_retryable(content):
    with pytest.raises(GroundingFailure) as caught:
        decode_answer("p", content, IDS)
    assert caught.value.kind == "malformed_response" and caught.value.retryable is True
    assert caught.value.provider == "p"


def test_abstention_needs_an_explanation():
    with pytest.raises(GroundingFailure, match="abstention requires an explanation") as caught:
        decode_answer("p", reply("insufficient_evidence", "  ", ()), IDS)
    assert caught.value.retryable is True


@pytest.mark.parametrize("content, message", [
    (reply(citations=("invented",)), "not retrieved"),
    (reply(citations=("c-1", "invented")), "not retrieved"),
    (reply(citations=()), "at least one citation"),
    (reply(answer="  "), "at least one citation"),
    (reply("insufficient_evidence", "No.", ("c-1",)), "abstention cannot cite"),
    (json.dumps({"status": "ok", "answer": "a", "citations": [1]}), "must be chunk IDs"),
])
def test_ungrounded_answers_are_not_retryable(content, message):
    with pytest.raises(GroundingFailure, match=message) as caught:
        decode_answer("p", content, IDS)
    assert caught.value.kind == "invalid_citations" and caught.value.retryable is False


def test_no_evidence_means_nothing_can_be_cited():
    with pytest.raises(GroundingFailure, match="not retrieved"):
        decode_answer("p", reply(), ())
    assert decode_answer("p", reply("insufficient_evidence", "No evidence.", ()), ()).status == "insufficient_evidence"


def test_embedded_json_objects():
    found = embedded_json_objects('x {"a": 1} y {broken {"b": {"c": 2}} z')
    assert found == ({"a": 1}, {"b": {"c": 2}})
    assert embedded_json_objects("nothing here") == ()


def test_schema_names_the_three_fields():
    assert ANSWER_JSON_SCHEMA["required"] == ["status", "answer", "citations"]
    assert ANSWER_JSON_SCHEMA["properties"]["status"]["enum"] == ["ok", "insufficient_evidence"]


# failures -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("status, kind, retryable", [
    (401, "authentication", True), (403, "authentication", True), (429, "rate_limit", True),
    (500, "provider_5xx", True), (503, "provider_5xx", True),
    (400, "request_rejected", False), (404, "request_rejected", False), (422, "request_rejected", False),
])
def test_status_failure(status, kind, retryable):
    failure = status_failure("prov", status)
    assert (failure.provider, failure.kind, failure.retryable) == ("prov", kind, retryable)
    assert str(failure) == "prov returned HTTP %d" % status
    assert failure.fallback_used is False and failure.initial_failure_kind is None and failure.context == {}


def test_a_grounding_failure_is_a_provider_failure():
    assert issubclass(GroundingFailure, ProviderFailure) and issubclass(ProviderFailure, RuntimeError)


def test_http_failure_kinds():
    request = httpx.Request("POST", "https://x.test")
    assert http_failure("p", httpx.ReadTimeout("slow", request=request)).kind == "timeout"
    assert http_failure("p", httpx.ConnectError("down", request=request)).kind == "connection"
    assert http_failure("p", httpx.HTTPStatusError("x", request=request, response=httpx.Response(502, request=request))).kind == "provider_5xx"
    assert http_failure("p", KeyError("choices")).kind == "malformed_response"


# router -------------------------------------------------------------------------------------------------

class Stub:
    def __init__(self, name, answer=None, failure=None):
        self.name, self.answer, self.failure, self.calls = name, answer, failure, []

    def generate(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.failure:
            raise self.failure
        return self.answer


def fail(kind="timeout", retryable=True, provider="a"):
    return ProviderFailure(provider, kind, kind, retryable=retryable)


def test_auto_uses_the_first_provider_when_it_works():
    a, b = Stub("a", OK), Stub("b", OK)
    out = FailoverRouter({"a": a, "b": b}).generate("auto", "q", EVIDENCE, None)
    assert (out.actual_provider, out.fallback_used, out.initial_failure_kind) == ("a", False, None)
    assert b.calls == [] and a.calls == [(("q", EVIDENCE, None), {})]


def test_auto_falls_over_on_a_retryable_failure_and_says_why():
    a, b = Stub("a", failure=fail("timeout")), Stub("b", OK)
    out = FailoverRouter({"a": a, "b": b}).generate("auto", "q", EVIDENCE)
    assert (out.actual_provider, out.fallback_used, out.initial_failure_kind) == ("b", True, "timeout")


def test_auto_stops_at_a_non_retryable_failure():
    a = Stub("a", failure=fail("request_rejected", retryable=False))
    b = Stub("b", OK)
    with pytest.raises(ProviderFailure) as caught:
        FailoverRouter({"a": a, "b": b}).generate("auto", "q", EVIDENCE)
    assert caught.value.fallback_used is False and caught.value.initial_failure_kind is None
    assert b.calls == []


def test_auto_does_not_fall_over_for_ungrounded_citations():
    a = Stub("a", failure=GroundingFailure("a", "invalid_citations", "bad", retryable=False))
    b = Stub("b", OK)
    with pytest.raises(GroundingFailure):
        FailoverRouter({"a": a, "b": b}).generate("auto", "q", EVIDENCE)
    assert b.calls == []


def test_the_failure_after_a_fallback_carries_the_first_kind():
    a = Stub("a", failure=fail("timeout", provider="a"))
    b = Stub("b", failure=fail("connection", provider="b"))
    with pytest.raises(ProviderFailure) as caught:
        FailoverRouter({"a": a, "b": b}).generate("auto", "q", EVIDENCE)
    assert (caught.value.provider, caught.value.kind) == ("b", "connection")
    assert (caught.value.fallback_used, caught.value.initial_failure_kind) == (True, "timeout")


def test_a_non_retryable_failure_of_the_fallback_is_marked_too():
    a = Stub("a", failure=fail("rate_limit"))
    b = Stub("b", failure=GroundingFailure("b", "invalid_citations", "bad", retryable=False))
    with pytest.raises(GroundingFailure) as caught:
        FailoverRouter({"a": a, "b": b}).generate("auto", "q", EVIDENCE)
    assert caught.value.fallback_used is True and caught.value.initial_failure_kind == "rate_limit"


def test_a_named_provider_is_called_once_and_never_switches():
    a, b = Stub("a", failure=fail("timeout")), Stub("b", OK)
    router = FailoverRouter({"a": a, "b": b})
    with pytest.raises(ProviderFailure) as caught:
        router.generate("a", "q", EVIDENCE)
    assert caught.value.fallback_used is False and len(a.calls) == 1 and b.calls == []
    out = router.generate("b", "q", EVIDENCE)
    assert (out.actual_provider, out.fallback_used) == ("b", False)
    with pytest.raises(KeyError):
        router.generate("c", "q", EVIDENCE)


def test_three_providers_in_a_custom_order():
    a, b, c = Stub("a", OK), Stub("b", failure=fail("timeout")), Stub("c", OK)
    router = FailoverRouter({"a": a, "b": b, "c": c}, order=["b", "c", "a"])
    out = router.generate("auto", "q", EVIDENCE)
    assert (out.actual_provider, out.initial_failure_kind) == ("c", "timeout") and a.calls == []
    b.failure = fail("rate_limit")
    c.failure = fail("provider_5xx")
    out = router.generate("auto", "q", EVIDENCE)
    assert (out.actual_provider, out.fallback_used, out.initial_failure_kind) == ("a", True, "rate_limit")


def test_a_different_word_for_auto():
    out = FailoverRouter({"a": Stub("a", OK)}, auto="any").generate("any", "q", EVIDENCE)
    assert out.actual_provider == "a"


def test_router_needs_known_providers():
    with pytest.raises(ValueError):
        FailoverRouter({"a": Stub("a")}, order=["a", "zzz"])
    with pytest.raises(ValueError):
        FailoverRouter({})


# HTTP providers (fake transports) -----------------------------------------------------------------------

def messages(question, evidence, scope):
    return [{"role": "system", "content": "sys"},
            {"role": "user", "content": "%s|%s|%s" % (question, ",".join(e.chunk_id for e in evidence), scope)}]


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def chat_reply(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_chat_completions_request_and_answer():
    seen = {}

    def handler(request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers.get("authorization"), json.loads(request.content)
        return chat_reply(reply())

    provider = ChatCompletionsProvider("svc", "https://api.example.test/v1/", "model-x", "KEY", messages,
                                       extra_body={"guided_json": ANSWER_JSON_SCHEMA}, client=client(handler))
    assert provider.generate("what?", EVIDENCE, "scope-1") == OK
    assert seen["url"] == "https://api.example.test/v1/chat/completions"
    assert seen["auth"] == "Bearer KEY"
    assert seen["body"]["model"] == "model-x" and seen["body"]["temperature"] == 0.1 and seen["body"]["max_tokens"] == 900
    assert seen["body"]["messages"][1]["content"] == "what?|c-1,c-2|scope-1"
    assert seen["body"]["guided_json"]["required"] == ["status", "answer", "citations"]
    assert list(seen["body"])[:4] == ["model", "messages", "temperature", "max_tokens"]


def test_chat_completions_without_a_key_fails_before_calling():
    called = []
    provider = ChatCompletionsProvider("svc", "https://api.example.test", "m", None, messages,
                                       client=client(lambda r: called.append(r) or chat_reply(reply())))
    with pytest.raises(ProviderFailure, match="svc API key is not configured") as caught:
        provider.generate("q", EVIDENCE)
    assert (caught.value.kind, caught.value.retryable) == ("authentication", True) and called == []
    custom = ChatCompletionsProvider("svc", "https://x.test", "m", "", messages, missing_key_message="SVC_KEY is not set")
    with pytest.raises(ProviderFailure, match="SVC_KEY is not set"):
        custom.generate("q", EVIDENCE)


def test_chat_completions_key_can_be_optional():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return chat_reply(reply())

    provider = ChatCompletionsProvider("local", "http://127.0.0.1:9", "m", None, messages, api_key_required=False,
                                       client=client(handler))
    assert provider.generate("q", EVIDENCE) == OK and seen["auth"] is None


def test_ollama_request_and_answer():
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": reply(citations=("c-2",))}})

    provider = OllamaChatProvider("local", "http://127.0.0.1:11434/", "m", messages, client=client(handler))
    assert provider.generate("q", EVIDENCE).cited_chunk_ids == ("c-2",)
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    assert seen["body"] == {"model": "m", "messages": messages("q", EVIDENCE, None), "stream": False,
                            "format": "json", "options": {"temperature": 0.1}}


@pytest.mark.parametrize("make", ["chat", "ollama"])
@pytest.mark.parametrize("response, kind, retryable", [
    (httpx.Response(401), "authentication", True),
    (httpx.Response(429), "rate_limit", True),
    (httpx.Response(500), "provider_5xx", True),
    (httpx.Response(400), "request_rejected", False),
    (httpx.Response(200, content=b"not json"), "malformed_response", True),
    (httpx.Response(200, json={"unexpected": 1}), "malformed_response", True),
    (httpx.Response(200, json={"choices": []}), "malformed_response", True),
])
def test_http_providers_map_errors(make, response, kind, retryable):
    c = client(lambda request: response)
    provider = (ChatCompletionsProvider("svc", "https://x.test", "m", "k", messages, client=c) if make == "chat"
                else OllamaChatProvider("svc", "https://x.test", "m", messages, client=c))
    with pytest.raises(ProviderFailure) as caught:
        provider.generate("q", EVIDENCE)
    assert (caught.value.provider, caught.value.kind, caught.value.retryable) == ("svc", kind, retryable)


@pytest.mark.parametrize("error, kind", [(httpx.ReadTimeout("slow"), "timeout"), (httpx.ConnectError("down"), "connection")])
def test_http_providers_map_transport_errors(error, kind):
    def handler(request):
        raise error

    provider = OllamaChatProvider("svc", "https://x.test", "m", messages, client=client(handler))
    with pytest.raises(ProviderFailure) as caught:
        provider.generate("q", EVIDENCE)
    assert caught.value.kind == kind and caught.value.retryable is True


def test_ungrounded_reply_through_a_provider_is_a_grounding_failure():
    provider = OllamaChatProvider("svc", "https://x.test", "m", messages,
                                  client=client(lambda r: httpx.Response(200, json={"message": {"content": reply(citations=("zzz",))}})))
    with pytest.raises(GroundingFailure, match="not retrieved"):
        provider.generate("q", EVIDENCE)


def test_a_non_object_reply_falls_over_to_the_next_provider_end_to_end():
    bad = ChatCompletionsProvider("a", "https://x.test", "m", "k", messages, client=client(lambda r: chat_reply("[]")))
    good = Stub("b", OK)
    out = FailoverRouter({"a": bad, "b": good}).generate("auto", "q", EVIDENCE)
    assert (out.actual_provider, out.fallback_used, out.initial_failure_kind) == ("b", True, "malformed_response")


def test_the_provider_closes_a_client_it_made_and_leaves_one_it_was_given(monkeypatch):
    made = []
    real = httpx.Client

    def factory(*args, **kwargs):
        c = real(*args, transport=httpx.MockTransport(lambda r: chat_reply(reply())), **{k: v for k, v in kwargs.items() if k != "transport"})
        made.append(c)
        return c

    monkeypatch.setattr(httpx, "Client", factory)
    provider = ChatCompletionsProvider("svc", "https://x.test", "m", "k", messages, timeout=5)
    provider.generate("q", EVIDENCE)
    assert len(made) == 1 and made[0].is_closed
    given = real(transport=httpx.MockTransport(lambda r: chat_reply(reply())))
    ChatCompletionsProvider("svc", "https://x.test", "m", "k", messages, client=given).generate("q", EVIDENCE)
    assert not given.is_closed
