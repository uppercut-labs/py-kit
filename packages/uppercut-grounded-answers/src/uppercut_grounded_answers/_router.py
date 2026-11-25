"""Try providers in order and move on only when a failure says another provider might do better."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol, Sequence

from ._answers import GeneratedAnswer
from ._failures import ProviderFailure


class GenerationProvider(Protocol):
    """Anything with a name and a `generate` that returns a GeneratedAnswer or raises ProviderFailure."""

    name: str

    def generate(self, *args: Any, **kwargs: Any) -> GeneratedAnswer: ...


@dataclass(frozen=True)
class ProviderOutcome:
    answer: GeneratedAnswer
    actual_provider: str
    fallback_used: bool
    initial_failure_kind: Optional[str] = None


class FailoverRouter:
    """Route a request to one named provider, or to the first of `order` that works.

    `choice` is a provider's name, or `auto`. A named provider is called once and its failure is raised as it is.
    With `auto` the providers in `order` are tried in turn: a retryable failure moves on to the next, a
    non-retryable one is raised at once. When one is tried after another failed, the outcome has
    `fallback_used=True` and `initial_failure_kind` names the first failure's kind. When every provider fails,
    the last failure is raised with the same two fields set, so a log or an API error shows that a fallback was
    tried and what started it.
    """

    def __init__(self, providers: Mapping[str, GenerationProvider], order: Optional[Sequence[str]] = None,
                 auto: str = "auto") -> None:
        self.providers = dict(providers)
        self.order = tuple(order if order is not None else self.providers)
        self.auto = auto
        unknown = [name for name in self.order if name not in self.providers]
        if unknown or not self.order:
            raise ValueError("order must name at least one known provider; unknown: %s" % ", ".join(unknown))

    def generate(self, choice: str, *args: Any, **kwargs: Any) -> ProviderOutcome:
        if choice != self.auto:
            if choice not in self.providers:
                raise KeyError(choice)
            answer = self.providers[choice].generate(*args, **kwargs)
            return ProviderOutcome(answer, choice, False)
        first_kind: Optional[str] = None
        for name in self.order:
            try:
                answer = self.providers[name].generate(*args, **kwargs)
            except ProviderFailure as exc:
                if first_kind is not None:
                    exc.fallback_used = True
                    exc.initial_failure_kind = first_kind
                if not exc.retryable or name == self.order[-1]:
                    raise
                if first_kind is None:
                    first_kind = exc.kind
                continue
            return ProviderOutcome(answer, name, first_kind is not None, first_kind)
        raise AssertionError("unreachable")  # the loop always returns or raises
