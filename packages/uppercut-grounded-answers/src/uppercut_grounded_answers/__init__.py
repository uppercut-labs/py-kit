"""Answer from retrieved evidence: typed provider failures, failover between providers, citation validation."""

from ._answers import ANSWER_JSON_SCHEMA, GeneratedAnswer, decode_answer, embedded_json_objects
from ._failures import (
    AUTHENTICATION,
    CONNECTION,
    INVALID_CITATIONS,
    MALFORMED_RESPONSE,
    PROVIDER_5XX,
    RATE_LIMIT,
    REQUEST_REJECTED,
    TIMEOUT,
    GroundingFailure,
    ProviderFailure,
    connection_failure,
    malformed_failure,
    status_failure,
    timeout_failure,
)
from ._http import BuildMessages, ChatCompletionsProvider, OllamaChatProvider, http_failure
from ._router import FailoverRouter, GenerationProvider, ProviderOutcome

__all__ = [
    "ANSWER_JSON_SCHEMA",
    "AUTHENTICATION",
    "BuildMessages",
    "CONNECTION",
    "ChatCompletionsProvider",
    "FailoverRouter",
    "GeneratedAnswer",
    "GenerationProvider",
    "GroundingFailure",
    "INVALID_CITATIONS",
    "MALFORMED_RESPONSE",
    "OllamaChatProvider",
    "PROVIDER_5XX",
    "ProviderFailure",
    "ProviderOutcome",
    "RATE_LIMIT",
    "REQUEST_REJECTED",
    "TIMEOUT",
    "connection_failure",
    "decode_answer",
    "embedded_json_objects",
    "http_failure",
    "malformed_failure",
    "status_failure",
    "timeout_failure",
]
__version__ = "1.0.0"
