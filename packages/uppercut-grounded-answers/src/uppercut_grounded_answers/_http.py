"""Providers that call a chat endpoint over HTTP. The endpoint, model and prompt are the caller's."""

from __future__ import annotations

from operator import attrgetter
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

import httpx

from ._answers import GeneratedAnswer, decode_answer
from ._failures import (
    ProviderFailure,
    connection_failure,
    malformed_failure,
    status_failure,
    timeout_failure,
)

# (question, evidence, scope) -> chat messages: [{"role": "system", "content": ...}, {"role": "user", ...}]
BuildMessages = Callable[[str, Any, Any], List[Dict[str, str]]]


def http_failure(provider: str, exc: Exception) -> ProviderFailure:
    """Map an httpx exception, or a bad response body, to a ProviderFailure.

    A timeout and a network error are `timeout` and `connection`; an HTTP status goes through `status_failure`;
    anything else (a missing field, a body that is not JSON) is `malformed_response`. All but a rejected
    request are retryable.
    """
    if isinstance(exc, httpx.TimeoutException):
        return timeout_failure(provider)
    if isinstance(exc, httpx.NetworkError):
        return connection_failure(provider)
    if isinstance(exc, httpx.HTTPStatusError):
        return status_failure(provider, exc.response.status_code)
    return malformed_failure(provider)


class _ChatProvider:
    def __init__(self, name: str, base_url: str, model: str, build_messages: BuildMessages, *,
                 client: Optional[httpx.Client], timeout: float, chunk_id_of: Callable[[Any], str]) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.build_messages = build_messages
        self._client = client
        self.timeout = timeout
        self._chunk_id_of = chunk_id_of

    def _post(self, url: str, body: Mapping[str, Any], headers: Optional[Mapping[str, str]], content_of: Callable[[Any], Any]) -> Any:
        client = self._client or httpx.Client(timeout=self.timeout)
        try:
            response = client.post(url, headers=dict(headers or {}), json=body)
            response.raise_for_status()
            return content_of(response.json())
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise http_failure(self.name, exc) from exc
        finally:
            if self._client is None:
                client.close()

    def _decode(self, content: object, evidence: Iterable[Any]) -> GeneratedAnswer:
        return decode_answer(self.name, content, {self._chunk_id_of(item) for item in evidence})


class ChatCompletionsProvider(_ChatProvider):
    """An OpenAI-style `POST {base_url}/chat/completions` endpoint with a bearer key.

    `extra_body` is merged into the request, for options a service adds (schema-guided output, template
    arguments). Without a key, and with `api_key_required=True`, `generate` raises a retryable
    `authentication` failure before any request is made; `missing_key_message` replaces its text.
    """

    def __init__(self, name: str, base_url: str, model: str, api_key: Optional[str], build_messages: BuildMessages, *,
                 extra_body: Optional[Mapping[str, Any]] = None, temperature: float = 0.1, max_tokens: int = 900,
                 timeout: float = 90.0, api_key_required: bool = True, missing_key_message: Optional[str] = None,
                 client: Optional[httpx.Client] = None, chunk_id_of: Callable[[Any], str] = attrgetter("chunk_id")) -> None:
        super().__init__(name, base_url, model, build_messages, client=client, timeout=timeout, chunk_id_of=chunk_id_of)
        self.api_key = api_key
        self.extra_body = dict(extra_body or {})
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_key_required = api_key_required
        self.missing_key_message = missing_key_message or f"{name} API key is not configured"

    def generate(self, question: str, evidence: Any, scope: Any = None) -> GeneratedAnswer:
        if self.api_key_required and not self.api_key:
            raise ProviderFailure(self.name, "authentication", self.missing_key_message, retryable=True)
        evidence = tuple(evidence)
        body = {
            "model": self.model,
            "messages": self.build_messages(question, evidence, scope),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            **self.extra_body,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        content = self._post(f"{self.base_url}/chat/completions", body, headers,
                             lambda data: data["choices"][0]["message"]["content"])
        return self._decode(content, evidence)


class OllamaChatProvider(_ChatProvider):
    """An Ollama `POST {base_url}/api/chat` endpoint, asked for one JSON reply."""

    def __init__(self, name: str, base_url: str, model: str, build_messages: BuildMessages, *,
                 temperature: float = 0.1, timeout: float = 180.0, client: Optional[httpx.Client] = None,
                 chunk_id_of: Callable[[Any], str] = attrgetter("chunk_id")) -> None:
        super().__init__(name, base_url, model, build_messages, client=client, timeout=timeout, chunk_id_of=chunk_id_of)
        self.temperature = temperature

    def generate(self, question: str, evidence: Any, scope: Any = None) -> GeneratedAnswer:
        evidence = tuple(evidence)
        body = {
            "model": self.model,
            "messages": self.build_messages(question, evidence, scope),
            "stream": False,
            "format": "json",
            "options": {"temperature": self.temperature},
        }
        content = self._post(f"{self.base_url}/api/chat", body, None, lambda data: data["message"]["content"])
        return self._decode(content, evidence)
