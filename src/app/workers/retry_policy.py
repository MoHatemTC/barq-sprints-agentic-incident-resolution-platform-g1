"""Retry policy for incident-processing workers (S2.3).

Configuration-driven exponential backoff and the transient/terminal failure
classification. This module is pure: no broker, no database, no clock — so the
graded behaviours (exact backoff sequence, classification verdicts) are
testable in isolation and stable across Celery versions.

Classification semantics
------------------------
Every failure answers one question: "could the exact same job succeed if it
were simply run again later?"

- YES -> :class:`RetryableError` (or a mapped builtin): the world hiccupped.
  Examples: LLM timeout, Qdrant unreachable, database connection dropped.
- NO  -> :class:`TerminalError`: the job itself is broken. Retrying wastes the
  worker pool and delays every healthy incident behind it. Examples: malformed
  payload, unknown event type, authentication denied.

Unknown/unexpected exceptions classify as TERMINAL (fail closed): an
unclassified error is a deterministic bug, and retrying a deterministic bug
only hides it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from celery.exceptions import SoftTimeLimitExceeded


class RetryableError(Exception):
    """Transient failure: the same job is likely to succeed on a later attempt.

    Examples: LLM/HTTP timeout, vector store unreachable, database connection
    dropped, network blip. A hang (soft time limit) is transient by definition.
    """


class TerminalError(Exception):
    """Permanent failure: retrying cannot ever succeed.

    Examples: malformed payload, event type outside the contract, identity
    denied. Dead-letters immediately without spending the retry budget.
    """


@dataclass(frozen=True, slots=True)
class RetryConfig:
    """Retry knobs, sourced from Settings — never literals in worker code."""

    max_retries: int
    backoff_base: float
    backoff_max: float
    jitter: bool


def build_retry_config(settings: object) -> RetryConfig:
    """Build a :class:`RetryConfig` from the application Settings.

    Typed loosely (duck-typed settings protocol) so tests can pass mock
    settings without importing the full Settings class.
    """
    return RetryConfig(
        max_retries=int(settings.worker_max_retries),  # type: ignore[attr-defined]
        backoff_base=float(settings.worker_backoff_base),  # type: ignore[attr-defined]
        backoff_max=float(settings.worker_backoff_max),  # type: ignore[attr-defined]
        jitter=bool(settings.worker_backoff_jitter),  # type: ignore[attr-defined]
    )


# Builtin exceptions that carry transient semantics. A soft time limit is a
# hang, and a hang is the world being slow — the textbook transient.
RETRYABLE_BUILTINS: tuple[type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
    SoftTimeLimitExceeded,
)


def classify(exc: BaseException) -> bool:
    """Return True if ``exc`` deserves another attempt, False to dead-letter.

    Unknown exception types are terminal: retrying a deterministic bug hides
    it from both the operator and the dead-letter review.
    """
    if isinstance(exc, RetryableError):
        return True
    if isinstance(exc, TerminalError):
        return False
    return isinstance(exc, RETRYABLE_BUILTINS)


def backoff_delay(attempt: int, cfg: RetryConfig) -> float:
    """Exponential backoff with optional full jitter, in seconds.

    Without jitter the sequence is exactly ``base * 2**(attempt - 1)`` for
    attempt = 1, 2, 3, ... capped at ``backoff_max``. With full jitter the
    delay is drawn uniformly from ``[0, uncapped_delay]`` (AWS-style), which
    spreads retriers that failed at the same moment.
    """
    if attempt < 1:
        raise ValueError("attempt is 1-based and must be >= 1")

    uncapped = cfg.backoff_base * (2 ** (attempt - 1))
    uncapped = min(uncapped, cfg.backoff_max)

    if not cfg.jitter:
        return uncapped
    return random.uniform(0.0, uncapped)


__all__ = [
    "RETRYABLE_BUILTINS",
    "RetryConfig",
    "RetryableError",
    "TerminalError",
    "backoff_delay",
    "build_retry_config",
    "classify",
]
