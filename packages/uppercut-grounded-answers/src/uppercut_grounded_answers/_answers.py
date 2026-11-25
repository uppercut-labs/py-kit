"""Decode a model's JSON answer and check that every citation points at evidence it was given."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Collection, Dict, List, Literal, Tuple

from ._failures import INVALID_CITATIONS, MALFORMED_RESPONSE, GroundingFailure

Status = Literal["ok", "insufficient_evidence"]

# The JSON the model is asked to return. Pass it to a provider that supports schema-guided output.
ANSWER_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok", "insufficient_evidence"]},
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "answer", "citations"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class GeneratedAnswer:
    status: Status
    answer: str
    cited_chunk_ids: Tuple[str, ...]


def embedded_json_objects(text: str) -> Tuple[object, ...]:
    """Every JSON value that starts at a `{` in `text`, in order. Text that is not JSON is skipped."""
    decoder = json.JSONDecoder()
    found: List[object] = []
    search_from = 0
    while True:
        start = text.find("{", search_from)
        if start < 0:
            return tuple(found)
        try:
            payload, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            search_from = start + 1
            continue
        found.append(payload)
        search_from = max(start + 1, end)


def _has_answer_fields(payload: object) -> bool:
    return isinstance(payload, dict) and {"status", "answer", "citations"} <= payload.keys()


def decode_answer(provider: str, content: object, valid_ids: Collection[str]) -> GeneratedAnswer:
    """Turn a model's reply into a GeneratedAnswer, or raise GroundingFailure.

    The reply may be bare JSON, JSON in a code fence, or JSON after other text such as a reasoning block; when
    several objects appear, the last one that has all three fields wins. The rules, and which failures a second
    provider could fix:

    - No text, invalid JSON, a non-object, wrong field types or an abstention without an explanation raise
      `malformed_response` with `retryable=True`.
    - A citation that is not in `valid_ids`, citations that are not strings, an `ok` answer with no citation or
      no text, or an abstention that cites anything raise `invalid_citations` with `retryable=False`.
    """
    if not isinstance(content, str) or not content.strip():
        raise GroundingFailure(provider, MALFORMED_RESPONSE, "provider returned no answer", retryable=True)
    candidate = content.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        embedded = [item for item in embedded_json_objects(candidate) if isinstance(item, dict)]
        if not embedded:
            raise GroundingFailure(provider, MALFORMED_RESPONSE, "provider returned invalid JSON", retryable=True)
        payload = next((item for item in reversed(embedded) if _has_answer_fields(item)), embedded[-1])
    if not isinstance(payload, dict):
        raise GroundingFailure(provider, MALFORMED_RESPONSE, "provider JSON must be an object", retryable=True)
    status = payload.get("status")
    answer = payload.get("answer")
    citations = payload.get("citations")
    if status not in ("ok", "insufficient_evidence") or not isinstance(answer, str) or not isinstance(citations, list):
        raise GroundingFailure(
            provider, MALFORMED_RESPONSE, "provider JSON does not match the answer schema", retryable=True
        )
    if not all(isinstance(item, str) for item in citations):
        raise GroundingFailure(provider, INVALID_CITATIONS, "citations must be chunk IDs", retryable=False)
    if set(citations) - set(valid_ids):
        raise GroundingFailure(
            provider, INVALID_CITATIONS, "provider cited evidence that was not retrieved", retryable=False
        )
    if status == "ok" and (not answer.strip() or not citations):
        raise GroundingFailure(
            provider, INVALID_CITATIONS, "grounded answer requires at least one citation", retryable=False
        )
    if status == "insufficient_evidence" and citations:
        raise GroundingFailure(provider, INVALID_CITATIONS, "abstention cannot cite evidence", retryable=False)
    if status == "insufficient_evidence" and not answer.strip():
        raise GroundingFailure(provider, MALFORMED_RESPONSE, "abstention requires an explanation", retryable=True)
    return GeneratedAnswer(status=status, answer=answer.strip(), cited_chunk_ids=tuple(dict.fromkeys(citations)))
