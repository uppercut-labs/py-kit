"""Typed provider failures. `retryable` says whether another provider should be tried."""

from __future__ import annotations

from typing import Any, Dict, Optional

# Failure kinds the package raises. A failure may carry any other string a caller invents.
TIMEOUT = "timeout"
CONNECTION = "connection"
AUTHENTICATION = "authentication"
RATE_LIMIT = "rate_limit"
PROVIDER_5XX = "provider_5xx"
REQUEST_REJECTED = "request_rejected"
MALFORMED_RESPONSE = "malformed_response"
INVALID_CITATIONS = "invalid_citations"


class ProviderFailure(RuntimeError):
    """A provider could not give a usable answer.

    `provider` is the provider's name and `kind` says what went wrong (see the constants above).
    `retryable` is True when a different provider might do better and False when it would not: a rejected
    request or an answer that cites evidence it was never given will fail the same way elsewhere.
    `fallback_used` and `initial_failure_kind` are set by the router on the failure it finally raises, so
    the caller can tell that another provider was tried first and why. `context` is for the caller's own
    data, such as the ids of records written about this failure.
    """

    def __init__(
        self,
        provider: str,
        kind: str,
        message: str,
        *,
        retryable: bool,
        fallback_used: bool = False,
        initial_failure_kind: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.kind = kind
        self.retryable = retryable
        self.fallback_used = fallback_used
        self.initial_failure_kind = initial_failure_kind
        self.context: Dict[str, Any] = {}


class GroundingFailure(ProviderFailure):
    """The provider answered, but the answer is malformed or is not grounded in the evidence it was given."""


def timeout_failure(provider: str) -> ProviderFailure:
    return ProviderFailure(provider, TIMEOUT, f"{provider} request timed out", retryable=True)


def connection_failure(provider: str) -> ProviderFailure:
    return ProviderFailure(provider, CONNECTION, f"{provider} is unreachable", retryable=True)


def malformed_failure(provider: str) -> ProviderFailure:
    return ProviderFailure(provider, MALFORMED_RESPONSE, f"{provider} returned an invalid response", retryable=True)


def status_failure(provider: str, status: int) -> ProviderFailure:
    """The failure for an HTTP error status.

    401 and 403 are `authentication`, 429 is `rate_limit` and 5xx is `provider_5xx`; all three are retryable,
    since another provider may be configured correctly. Any other status is `request_rejected` and is not
    retryable.
    """
    if status in (401, 403):
        kind = AUTHENTICATION
    elif status == 429:
        kind = RATE_LIMIT
    elif status >= 500:
        kind = PROVIDER_5XX
    else:
        kind = REQUEST_REJECTED
    return ProviderFailure(provider, kind, f"{provider} returned HTTP {status}", retryable=kind != REQUEST_REJECTED)
