# uppercut-grounded-answers

The part of a retrieval-augmented answer service that does not depend on what the documents are. A model is asked to answer from evidence you retrieved, and returns JSON with a status, an answer and the ids of the pieces of evidence it used. This package decodes that reply, rejects an answer that cites evidence the model was never given, turns every way a provider can fail into a typed `ProviderFailure` that says whether another provider is worth trying, and tries providers in order. It includes two small HTTP providers, one for OpenAI-style chat completion endpoints and one for Ollama. Endpoints, model names, keys and the prompt are all arguments: nothing about a particular service, model or subject is in the package.

```sh
pip install uppercut-grounded-answers
```

```python
from uppercut_grounded_answers import (
    ANSWER_JSON_SCHEMA, ChatCompletionsProvider, FailoverRouter, OllamaChatProvider, ProviderFailure,
)

def build_messages(question, evidence, scope):
    excerpts = "\n\n".join(f"CHUNK_ID: {e.chunk_id}\nTEXT:\n{e.text}" for e in evidence)
    return [
        {"role": "system", "content": "Answer only from the excerpts. Return JSON: "
                                      '{"status":"ok|insufficient_evidence","answer":"...","citations":["chunk id"]}'},
        {"role": "user", "content": f"QUESTION:\n{question}\n\nEXCERPTS:\n{excerpts}"},
    ]

router = FailoverRouter({
    "hosted": ChatCompletionsProvider("hosted", "https://api.example.test/v1", "model-a", api_key, build_messages,
                                      extra_body={"guided_json": ANSWER_JSON_SCHEMA}),
    "local": OllamaChatProvider("local", "http://127.0.0.1:11434", "model-b", build_messages),
})

try:
    outcome = router.generate("auto", question, evidence, scope=None)   # "hosted", "local" or "auto"
except ProviderFailure as failure:
    print(failure.provider, failure.kind, failure.retryable, failure.fallback_used, failure.initial_failure_kind)
else:
    print(outcome.answer.status, outcome.answer.answer, outcome.answer.cited_chunk_ids)
    print(outcome.actual_provider, outcome.fallback_used, outcome.initial_failure_kind)
```

`evidence` is any sequence of objects with a `chunk_id` attribute (pass `chunk_id_of=` to a provider to read the id another way). The router forwards everything after the choice to the provider's `generate`, so your own providers can take any arguments.

## What counts as a failure, and whether to fail over

| Kind | Raised when | Retryable |
| --- | --- | --- |
| `timeout` | The request timed out | yes |
| `connection` | The provider could not be reached | yes |
| `authentication` | HTTP 401 or 403, or no API key | yes: another provider may be set up correctly |
| `rate_limit` | HTTP 429 | yes |
| `provider_5xx` | HTTP 500 or above | yes |
| `request_rejected` | Any other HTTP error status | no: it would be rejected elsewhere too |
| `malformed_response` | No text, not JSON, not an object, wrong fields, or an abstention with no explanation | yes |
| `invalid_citations` | A citation that was not in the evidence, citations that are not strings, an `ok` answer with no citation, an abstention that cites something | no: a second model would be trusted no more than the first, and the caller should see it |

`FailoverRouter.generate("auto", ...)` moves to the next provider only after a retryable failure. A non-retryable failure is raised at once. When a provider after the first answers, the outcome has `fallback_used=True` and `initial_failure_kind` set to the first failure's kind. When every provider fails, the last failure is raised with the same two fields set. A named provider (`generate("local", ...)`) is called once and never switches.

## The reply format

The model is asked for `{"status": "ok" | "insufficient_evidence", "answer": "...", "citations": ["id", ...]}`; `ANSWER_JSON_SCHEMA` is that as a JSON Schema for services that can enforce it. `decode_answer` accepts the JSON bare, in a code fence, or after other text such as a reasoning block. If several objects appear, the last one that has all three fields wins, so a draft that the model wrote before its final answer is ignored. The answer text is stripped and repeated citations are dropped, keeping the order.

## API

| Name | What it does |
| --- | --- |
| `decode_answer(provider, content, valid_ids)` | Reply text to `GeneratedAnswer(status, answer, cited_chunk_ids)`, or `GroundingFailure` |
| `ANSWER_JSON_SCHEMA` | The reply format as a JSON Schema |
| `ProviderFailure(provider, kind, message, *, retryable, fallback_used=False, initial_failure_kind=None)` | The error every provider raises. `.context` is an empty dict for your own data (record ids, for example). A `RuntimeError` |
| `GroundingFailure` | A `ProviderFailure` for a reply that is malformed or ungrounded |
| `status_failure(provider, status)`, `timeout_failure`, `connection_failure`, `malformed_failure` | Build the failure for an HTTP status, a timeout, a connection error or a bad body |
| `http_failure(provider, exc)` | Map an `httpx` exception (or a missing field or bad JSON in a response) to a failure |
| `FailoverRouter(providers, order=None, auto="auto")` | Try providers by name, or in `order` for `auto`. `.generate(choice, *args, **kwargs)` returns a `ProviderOutcome(answer, actual_provider, fallback_used, initial_failure_kind)` |
| `ChatCompletionsProvider(name, base_url, model, api_key, build_messages, *, extra_body=None, temperature=0.1, max_tokens=900, timeout=90, api_key_required=True, missing_key_message=None, client=None, chunk_id_of=...)` | `POST {base_url}/chat/completions` with `Authorization: Bearer` |
| `OllamaChatProvider(name, base_url, model, build_messages, *, temperature=0.1, timeout=180, client=None, chunk_id_of=...)` | `POST {base_url}/api/chat` with `stream: false` and `format: "json"` |

`build_messages(question, evidence, scope)` returns the chat messages. The prompt is yours: what the evidence is, how it is labelled, what to do when it is not enough. Pass `client=` an `httpx.Client` to control proxies or to test with `httpx.MockTransport`; a client the provider makes itself is closed after each call.

## Notes

- Waiting and retrying the same provider is a different job. Wrap the provider's request with a retry helper (such as `uppercut-http-retry`) if you want a short backoff before failing over; this package fails over at once.
- A failure message names the provider and the problem and never includes a response body, a header or a key.
- Whether the evidence supports the answer is not checked, only that every citation is evidence that was supplied and that an `ok` answer cites something.

## License

MIT
