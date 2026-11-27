"""Scrub credential-shaped text and mask or drop sensitive keys before they reach a log, an error or telemetry."""

from ._redact import (
    PLACEHOLDER,
    SENSITIVE_WORDS,
    is_sensitive_key,
    redact_mapping,
    redact_text,
)

__all__ = [
    "PLACEHOLDER",
    "SENSITIVE_WORDS",
    "is_sensitive_key",
    "redact_mapping",
    "redact_text",
]
__version__ = "1.0.0"
