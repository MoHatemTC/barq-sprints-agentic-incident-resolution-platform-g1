"""Chat cost control: a chat-specific daily allowance with atomic reservation.

Paid chat processing is refused unless both verified proxy price rates are
configured (``ChatSettings.chat_price_input_per_mtok`` / ``..._output_per_mtok``):
an estimate built from guessed list prices is not a budget. The reserve is
decremented from the day's allowance atomically (single Redis Lua script) so
concurrent turns cannot overspend the same remaining allocation; actual usage
is reconciled afterwards — a refund when the turn cost less than reserved.

This cap covers chat model calls only (screening, routing, answering,
verification, retries). Spending by the incident pipeline or evaluations is
out of scope.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from agent.llm import LLMClient, UsageSink
from app.chat.config import ChatSettings

logger = structlog.get_logger(__name__)

BUDGET_KEY_PREFIX = "barq:chat:budget:"

#: Keys outlive the UTC day they cover so a late reconciliation still lands.
_BUDGET_KEY_TTL_SECONDS = 172800

#: Reserved for one turn: screening (PII + classifier), routing, answering and
#: one bounded repair. Input size is estimated at ~4 chars/token on top of a
#: fixed prompt/system overhead.
_EXPECTED_MODEL_CALLS = 5
_PROMPT_OVERHEAD_CHARS = 4000
_CHARS_PER_TOKEN = 4.0

_RESERVE_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local limit = tonumber(ARGV[1])
local reserve = tonumber(ARGV[2])
if current + reserve > limit then
  return -1
end
redis.call('INCRBYFLOAT', KEYS[1], reserve)
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return 1
"""


class ChatBudgetExceeded(Exception):
    """The day's chat allocation is spent."""


class ChatBudgetUnavailable(Exception):
    """Budget accounting cannot run (missing rates or a Redis outage)."""


class ChatBudget(Protocol):
    """Budget seam; production is RedisChatBudget, tests script outcomes."""

    def reserve(self, amount_usd: float) -> None: ...

    def reconcile(self, actual_usd: float, reserved_usd: float) -> None: ...


class RedisChatBudget:
    """UTC-daily allowance in Redis; reserve is atomic across processes."""

    def __init__(self, redis: Any, *, daily_limit_usd: float) -> None:
        self._redis = redis
        self._limit = daily_limit_usd

    def _key(self) -> str:
        return BUDGET_KEY_PREFIX + datetime.now(UTC).strftime("%Y%m%d")

    def reserve(self, amount_usd: float) -> None:
        try:
            result = self._redis.eval(
                _RESERVE_LUA, 1, self._key(), self._limit, amount_usd, _BUDGET_KEY_TTL_SECONDS
            )
        except Exception as exc:  # fail closed: no accounting, no paid processing
            raise ChatBudgetUnavailable(f"budget counter unavailable: {type(exc).__name__}") from exc
        if result == -1:
            raise ChatBudgetExceeded("daily chat budget exhausted")

    def reconcile(self, actual_usd: float, reserved_usd: float) -> None:
        delta = actual_usd - reserved_usd
        try:
            self._redis.incrbyfloat(self._key(), delta)
            self._redis.expire(self._key(), _BUDGET_KEY_TTL_SECONDS)
        except Exception as exc:
            # The reserve already bounded worst-case spend; a failed reconcile
            # must never fail the completed turn. It only skews the counter
            # conservatively (over-counts).
            logger.warning("chat_budget_reconcile_failed", error=type(exc).__name__)


def estimate_turn_reserve(settings: ChatSettings, input_chars: int) -> float:
    """Worst-case USD estimate reserved before any model call of the turn."""
    if not settings.budget_configured:
        raise ChatBudgetUnavailable("verified chat price rates are not configured")
    assert settings.chat_price_input_per_mtok is not None
    assert settings.chat_price_output_per_mtok is not None
    input_tokens = (input_chars + _PROMPT_OVERHEAD_CHARS) / _CHARS_PER_TOKEN
    per_call = (
        input_tokens * settings.chat_price_input_per_mtok
        + settings.chat_max_output_tokens * settings.chat_price_output_per_mtok
    ) / 1_000_000
    return per_call * _EXPECTED_MODEL_CALLS


def actual_usage_cost(settings: ChatSettings, usage_records: list[dict[str, Any]]) -> float:
    """Reconciled cost of a turn's model calls.

    Proxy-reported costs win when every call reported one; otherwise the
    configured rates price the observed tokens. Estimates and reported costs
    are distinguishable by the ``cost_usd`` field each record carries.
    """
    if usage_records and all(record.get("cost_usd") is not None for record in usage_records):
        return float(sum(float(record["cost_usd"]) for record in usage_records))
    input_rate = settings.chat_price_input_per_mtok or 0.0
    output_rate = settings.chat_price_output_per_mtok or 0.0
    return sum(
        (
            float(record.get("input_tokens", 0)) * input_rate
            + float(record.get("output_tokens", 0)) * output_rate
        )
        / 1_000_000
        for record in usage_records
    )


class UsageRecordingLLM:
    """LLMClient wrapper that records per-call usage for reconciliation.

    Wrapping (rather than editing call sites) is what captures the screening
    calls made inside the guardrail helpers: every ``structured`` call through
    this wrapper — including PII detection and injection classification —
    lands a usage record.
    """

    def __init__(self, base: LLMClient, records: list[dict[str, Any]]) -> None:
        self._base = base
        self._records = records

    @property
    def model_name(self) -> str:
        return self._base.model_name

    def model_for_purpose(self, purpose: str, override: str | None = None) -> str:
        return self._base.model_for_purpose(purpose, override)

    def structured(
        self,
        *,
        purpose: str,
        system: str,
        prompt: str,
        schema: Any,
        model: str | None = None,
        trace_content: bool = True,
        max_retries: int | None = None,
        usage_sink: UsageSink | None = None,
    ) -> Any:
        def _record(entry: dict[str, Any]) -> None:
            self._records.append(entry)
            if usage_sink is not None:
                usage_sink(entry)

        return self._base.structured(
            purpose=purpose,
            system=system,
            prompt=prompt,
            schema=schema,
            model=model,
            trace_content=trace_content,
            max_retries=max_retries,
            usage_sink=_record,
        )


def new_turn_idempotency_token() -> str:
    """A server-side fallback request id (clients should send their own)."""
    return secrets.token_urlsafe(16)


__all__ = [
    "BUDGET_KEY_PREFIX",
    "ChatBudget",
    "ChatBudgetExceeded",
    "ChatBudgetUnavailable",
    "RedisChatBudget",
    "UsageRecordingLLM",
    "actual_usage_cost",
    "estimate_turn_reserve",
    "new_turn_idempotency_token",
]
