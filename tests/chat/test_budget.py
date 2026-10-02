"""Tests for the chat budget gate and usage reconciliation."""

from __future__ import annotations

import pytest

from agent.prompts import InjectionClassification, PIIDetectionOutput
from app.chat.budget import (
    ChatBudgetExceeded,
    ChatBudgetUnavailable,
    RedisChatBudget,
    UsageRecordingLLM,
    actual_usage_cost,
    estimate_turn_reserve,
)
from app.chat.config import ChatSettings
from tests.agent_support import FakeLLM

_CONFIGURED = ChatSettings(
    _env_file=None,
    chat_price_input_per_mtok=0.5,
    chat_price_output_per_mtok=2.0,
    chat_max_output_tokens=2000,
)
_UNCONFIGURED = ChatSettings(_env_file=None)


class FakeBudgetRedis:
    """Hand-rolled semantics of the single reserve script — nothing more."""

    def __init__(self) -> None:
        self.store: dict[str, float] = {}
        self.reserve_script_calls = 0

    def eval(self, script: str, numkeys: int, key: str, limit: str, amount: str, ttl: str) -> int:
        self.reserve_script_calls += 1
        current = self.store.get(key, 0.0)
        if current + float(amount) > float(limit):
            return -1
        self.store[key] = current + float(amount)
        return 1

    def incrbyfloat(self, key: str, delta: float) -> float:
        self.store[key] = self.store.get(key, 0.0) + delta
        return self.store[key]

    def expire(self, key: str, ttl: int) -> bool:
        return True


def test_reserve_admits_until_the_limit_is_reached() -> None:
    redis = FakeBudgetRedis()
    budget = RedisChatBudget(redis, daily_limit_usd=0.01)

    budget.reserve(0.006)
    with pytest.raises(ChatBudgetExceeded):
        budget.reserve(0.006)  # second reservation would exceed the $0.01 day
    assert redis.reserve_script_calls == 2


def test_reconcile_refunds_the_unspent_reserve() -> None:
    redis = FakeBudgetRedis()
    budget = RedisChatBudget(redis, daily_limit_usd=6.0)

    budget.reserve(0.05)
    budget.reconcile(actual_usd=0.02, reserved_usd=0.05)

    key = next(iter(redis.store))
    assert redis.store[key] == pytest.approx(0.02)


def test_reconcile_failure_never_raises() -> None:
    class _BrokenRedis:
        def eval(self, *_args: object, **_kwargs: object) -> int:
            return 1

        def incrbyfloat(self, *_args: object) -> float:
            raise ConnectionError("redis down")

        def expire(self, *_args: object) -> bool:
            return True

    budget = RedisChatBudget(_BrokenRedis(), daily_limit_usd=6.0)
    budget.reserve(0.05)
    budget.reconcile(actual_usd=0.02, reserved_usd=0.05)  # must not raise


def test_redis_outage_fails_closed() -> None:
    class _DownRedis:
        def eval(self, *_args: object, **_kwargs: object) -> int:
            raise ConnectionError("redis down")

    budget = RedisChatBudget(_DownRedis(), daily_limit_usd=6.0)

    with pytest.raises(ChatBudgetUnavailable):
        budget.reserve(0.01)


def test_estimate_requires_configured_rates() -> None:
    with pytest.raises(ChatBudgetUnavailable):
        estimate_turn_reserve(_UNCONFIGURED, input_chars=500)

    reserve = estimate_turn_reserve(_CONFIGURED, input_chars=800)
    expected = 5 * ((800 + 4000) / 4 * 0.5 + 2000 * 2.0) / 1_000_000
    assert reserve == pytest.approx(expected)


def test_actual_cost_prefers_proxy_reported_prices() -> None:
    records = [
        {"input_tokens": 900, "output_tokens": 150, "cost_usd": 0.004},
        {"input_tokens": 900, "output_tokens": 150, "cost_usd": 0.003},
    ]
    assert actual_usage_cost(_CONFIGURED, records) == pytest.approx(0.007)


def test_actual_cost_estimates_from_rates_when_costs_missing() -> None:
    records = [{"input_tokens": 1000, "output_tokens": 500, "cost_usd": None}]
    assert actual_usage_cost(_CONFIGURED, records) == pytest.approx(
        (1000 * 0.5 + 500 * 2.0) / 1_000_000
    )


def test_usage_recording_llm_captures_guardrail_calls() -> None:
    records: list[dict] = []
    base = FakeLLM(
        answers={
            "pii_detection": PIIDetectionOutput(findings=[]),
            "injection_classifier": InjectionClassification(is_injection=False, reason="ok"),
        }
    )
    wrapped = UsageRecordingLLM(base, records)  # type: ignore[arg-type]

    from app.chat.screening import screen_chat_input

    screening = screen_chat_input(wrapped, "A clean question about the VPN policy.")

    assert not screening.blocked
    assert [record["purpose"] for record in records] == ["pii_detection", "injection_classifier"]
