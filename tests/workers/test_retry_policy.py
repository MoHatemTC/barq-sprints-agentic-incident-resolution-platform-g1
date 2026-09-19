"""Unit tests for the retry policy: backoff formula and failure classification.

Pure tests — no broker, no database. The backoff formula and the transient/
terminal split are the graded heart of S2.3 and must be provable in isolation,
so they remain provable when Celery internals or the broker change.
"""

from __future__ import annotations

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from app.workers.retry_policy import (
    RetryableError,
    RetryConfig,
    TerminalError,
    backoff_delay,
    build_retry_config,
    classify,
)
from tests.helpers import mock_settings


class TestBackoffFormula:
    """The exponential formula must hold exactly when jitter is disabled:
    delay(attempt) = base * 2**(attempt - 1), capped at backoff_max."""

    def test_exact_sequence_without_jitter(self) -> None:
        cfg = RetryConfig(max_retries=5, backoff_base=1.0, backoff_max=60.0, jitter=False)

        assert [backoff_delay(a, cfg) for a in range(1, 6)] == [1.0, 2.0, 4.0, 8.0, 16.0]

    def test_doubles_with_non_unit_base(self) -> None:
        cfg = RetryConfig(max_retries=5, backoff_base=0.05, backoff_max=60.0, jitter=False)

        assert [backoff_delay(a, cfg) for a in range(1, 5)] == [0.05, 0.1, 0.2, 0.4]

    def test_capped_at_backoff_max(self) -> None:
        cfg = RetryConfig(max_retries=10, backoff_base=1.0, backoff_max=20.0, jitter=False)

        assert backoff_delay(5, cfg) == 16.0
        assert backoff_delay(6, cfg) == 20.0
        assert backoff_delay(9, cfg) == 20.0

    def test_jitter_stays_within_full_jitter_bounds(self) -> None:
        """Full jitter: delay is uniform in [0, uncapped_delay] — never above it.
        attempt 6 with base 2.0 -> uncapped = 2.0 * 2**5 = 64 (== backoff_max)."""
        cfg = RetryConfig(max_retries=6, backoff_base=2.0, backoff_max=64.0, jitter=True)

        for _ in range(200):
            delay = backoff_delay(6, cfg)
            assert 0.0 <= delay <= 64.0

    def test_jitter_is_randomised(self) -> None:
        cfg = RetryConfig(max_retries=6, backoff_base=2.0, backoff_max=64.0, jitter=True)

        delays = {backoff_delay(6, cfg) for _ in range(50)}
        assert len(delays) > 10  # not stuck on a single value

    def test_first_attempt_minimum_delay(self) -> None:
        """Even attempt 1 waits at least some delay — a truly immediate retry
        would hammer a struggling dependency."""
        cfg = RetryConfig(max_retries=5, backoff_base=1.0, backoff_max=60.0, jitter=False)

        assert backoff_delay(1, cfg) >= 1.0


class TestClassification:
    """Transient failures retry; terminal ones dead-letter immediately.
    Unknown exceptions fail CLOSED to terminal: an unclassified error is a
    deterministic bug, and retrying a deterministic bug only hides it."""

    def test_our_retryable_error_is_retryable(self) -> None:
        assert classify(RetryableError("llm timeout")) is True

    def test_our_terminal_error_is_not_retryable(self) -> None:
        assert classify(TerminalError("malformed payload")) is False

    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (TimeoutError("upstream timed out"), True),
            (TimeoutError("timed out"), True),  # builtin timeout alias
            (ConnectionError("connection refused"), True),
            (SoftTimeLimitExceeded(), True),  # a hang IS transient
            (ValueError("bad value"), False),  # unknown → terminal
            (KeyError("missing"), False),
            (RuntimeError("unexpected"), False),
        ],
    )
    def test_builtin_classification(self, exc: Exception, expected: bool) -> None:
        assert classify(exc) is expected

    def test_subclasses_inherit_classification(self) -> None:
        class QdrantUnavailable(ConnectionError):
            pass

        assert classify(QdrantUnavailable("qdrant down")) is True


class TestBuildRetryConfig:
    """Every retry knob comes from Settings — no literals in worker code."""

    def test_config_reflects_settings(self) -> None:
        settings = mock_settings(
            worker_max_retries=7,
            worker_backoff_base=0.5,
            worker_backoff_max=30.0,
            worker_backoff_jitter=False,
        )

        cfg = build_retry_config(settings)

        assert cfg == RetryConfig(
            max_retries=7,
            backoff_base=0.5,
            backoff_max=30.0,
            jitter=False,
        )

    def test_config_defaults_from_settings(self) -> None:
        cfg = build_retry_config(mock_settings())

        assert cfg.max_retries == 5
        assert cfg.backoff_base == 1.0
        assert cfg.jitter is True

    def test_source_has_no_hardcoded_retry_literals(self) -> None:
        """Metatest: the policy module must not contain magic retry numbers —
        values flow from configuration only (S2.3 brief requirement)."""
        import inspect

        import app.workers.retry_policy as module

        source = inspect.getsource(module)
        for forbidden in ("max_retries=5", "retry_backoff=1", "backoff_base=1.0"):
            assert forbidden not in source
