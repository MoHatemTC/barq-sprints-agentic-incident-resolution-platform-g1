"""Tests for the chat budget gate, reservation records and the model gateway."""

from __future__ import annotations

import pytest

from agent.llm import TerminalError
from agent.prompts import InjectionClassification, PIIWordDetectionOutput
from app.chat.budget import (
    ChatBudgetExceeded,
    ChatBudgetUnavailable,
    ChatModelGateway,
    RedisChatBudget,
    actual_usage_cost,
    estimate_turn_reserve,
)
from app.chat.config import ChatSettings
from app.chat.screening import screen_chat_input
from tests.agent_support import FakeLLM

_CONFIGURED = ChatSettings(
    _env_file=None,
    chat_price_input_per_mtok=0.5,
    chat_price_output_per_mtok=2.0,
    chat_max_output_tokens=2000,
)
_UNCONFIGURED = ChatSettings(_env_file=None)


class FakeBudgetRedis:
    """Hand-rolled semantics of the reserve script plus SET NX — nothing more."""

    def __init__(self) -> None:
        self.store: dict[str, float] = {}
        self.reserve_script_calls = 0
        self.markers: set[str] = set()

    def eval(self, script: str, numkeys: int, key: str, limit: str, amount: str, ttl: str) -> int:
        self.reserve_script_calls += 1
        current = self.store.get(key, 0.0)
        if current + float(amount) > float(limit):
            return -1
        self.store[key] = current + float(amount)
        return 1

    def set(self, key: str, value: object, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.markers:
            return False
        self.markers.add(key)
        return True

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
    reservation = budget.reserve(0.05)

    budget.reconcile(reservation, actual_usd=0.02)

    assert redis.store[reservation.day_key] == pytest.approx(0.02)


def test_reconcile_never_refunds_twice() -> None:
    redis = FakeBudgetRedis()
    budget = RedisChatBudget(redis, daily_limit_usd=6.0)
    reservation = budget.reserve(0.05)

    budget.reconcile(reservation, actual_usd=0.02)
    budget.reconcile(reservation, actual_usd=0.02)  # duplicate finalization

    assert redis.store[reservation.day_key] == pytest.approx(0.02)


def test_reconcile_hits_the_reserved_day_not_the_current_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A turn crossing UTC midnight refunds the day that was charged."""
    redis = FakeBudgetRedis()
    budget = RedisChatBudget(redis, daily_limit_usd=6.0)
    reservation = budget.reserve(0.05)

    from datetime import UTC, datetime, timedelta

    from app.chat import budget as budget_module

    tomorrow = datetime.now(UTC) + timedelta(days=1)
    monkeypatch.setattr(
        budget_module,
        "datetime",
        type("FrozenDateTime", (), {"now": classmethod(lambda cls, tz: tomorrow)}),
    )

    budget.reconcile(reservation, actual_usd=0.01)

    # The reserve put 0.05 on yesterday's key; reconciliation adjusts that key
    # down to the actual 0.01 — the new day's counter must not exist at all.
    assert redis.store[reservation.day_key] == pytest.approx(0.01)
    tomorrow_key = budget_module.BUDGET_KEY_PREFIX + tomorrow.strftime("%Y%m%d")
    assert tomorrow_key not in redis.store


def test_reconcile_failure_never_raises() -> None:
    class _BrokenRedis:
        def eval(self, *_args: object, **_kwargs: object) -> int:
            return 1

        def set(self, *_args: object, **_kwargs: object) -> bool:
            return True

        def incrbyfloat(self, *_args: object) -> float:
            raise ConnectionError("redis down")

        def expire(self, *_args: object) -> bool:
            return True

    budget = RedisChatBudget(_BrokenRedis(), daily_limit_usd=6.0)
    reservation = budget.reserve(0.05)
    budget.reconcile(reservation, actual_usd=0.02)  # must not raise


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
    expected = 6 * ((_CONFIGURED.chat_max_prompt_chars * 4 + 4096) * 0.5 + 2000 * 2.0) / 1_000_000
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


def test_partial_proxy_reporting_preserves_each_reported_cost() -> None:
    records = [
        {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.2},
        {"input_tokens": 1000, "output_tokens": 500, "cost_usd": None},
    ]
    assert actual_usage_cost(_CONFIGURED, records) == pytest.approx(0.2015)


def test_gateway_dispatches_the_configured_chat_model_for_guardrails() -> None:
    base = FakeLLM(answers={"pii_detection": PIIWordDetectionOutput(findings=[])})
    settings = ChatSettings(_env_file=None, chat_model="gemini/test-chat")
    gateway = ChatModelGateway(base, settings)
    gateway.structured(
        purpose="pii_detection", system="s", prompt="p", schema=PIIWordDetectionOutput
    )
    assert base.calls[0]["model"] == "gemini/test-chat"


def test_gateway_captures_guardrail_calls() -> None:
    base = FakeLLM(
        answers={
            "pii_detection": PIIWordDetectionOutput(findings=[]),
            "injection_classifier": InjectionClassification(is_injection=False, reason="ok"),
        }
    )
    gateway = ChatModelGateway(base, _CONFIGURED)  # type: ignore[arg-type]

    screening = screen_chat_input(gateway, "A clean question about the VPN policy.")

    assert not screening.blocked
    assert [record["purpose"] for record in gateway.records] == [
        "pii_detection",
        "injection_classifier",
    ]


def test_gateway_caps_output_and_disables_retries() -> None:
    from agent.prompts import PIIWordDetectionOutput

    base = FakeLLM(answers={"chat_route": PIIWordDetectionOutput(findings=[])})
    settings = ChatSettings(
        _env_file=None,
        chat_max_output_tokens=2000,
        chat_max_prompt_chars=60_000,
    )
    gateway = ChatModelGateway(base, settings)  # type: ignore[arg-type]

    gateway.structured(
        purpose="chat_route",
        system="s",
        prompt="p",
        schema=PIIWordDetectionOutput,
        max_completion_tokens=999_999,
    )

    call = base.calls[0]
    assert call["max_retries"] == 0, "implicit SDK retries must be off for chat"
    assert call["max_completion_tokens"] == 2000


def test_gateway_rejects_overbound_prompts_without_dispatch() -> None:
    base = FakeLLM(answers={})
    settings = ChatSettings(_env_file=None, chat_max_prompt_chars=100)
    gateway = ChatModelGateway(base, settings)  # type: ignore[arg-type]

    with pytest.raises(TerminalError):
        gateway.structured(purpose="chat_route", system="x" * 200, prompt="", schema=dict)

    assert base.calls == [] and gateway.records == []


def test_gateway_keeps_conservative_estimate_when_usage_is_unknown() -> None:
    """A call that fails before reporting usage is not refunded as zero."""
    base = FakeLLM(answers={"chat_route": TerminalError("model rejected")})
    settings = ChatSettings(_env_file=None, chat_max_prompt_chars=60_000)
    gateway = ChatModelGateway(base, settings)  # type: ignore[arg-type]

    with pytest.raises(TerminalError):
        gateway.structured(purpose="chat_route", system="s", prompt="p", schema=dict)

    assert len(gateway.records) == 1
    record = gateway.records[0]
    assert record["input_tokens"] > 0 and record["output_tokens"] > 0
    assert actual_usage_cost(_CONFIGURED, gateway.records) > 0
