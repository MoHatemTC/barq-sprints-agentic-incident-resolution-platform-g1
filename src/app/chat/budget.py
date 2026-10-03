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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from agent.llm import LLMClient, TerminalError, UsageSink
from app.chat.config import ChatSettings

logger = structlog.get_logger(__name__)

BUDGET_KEY_PREFIX = "barq:chat:budget:"

#: Keys outlive the UTC day they cover so a late reconciliation still lands.
_BUDGET_KEY_TTL_SECONDS = 172800

#: Reserved for one turn: screening (PII + classifier), routing, answering,
#: one bounded repair and one older-history summarization. Input size is
#: estimated at ~4 chars/token on top of the bounded worst case: system
#: prompts, the evidence block (``chat_max_evidence_chars``) and up to 6
#: history messages of 6,000 chars each — 12,000 + 36,000 + ~4,000 chars.
_EXPECTED_MODEL_CALLS = 6
# Upper byte bound per Unicode character, plus schema/chat framing allowance.
# This is deliberately conservative for unknown billed usage, not a tokenizer estimate.
_REQUEST_OVERHEAD_TOKENS = 4096

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


@dataclass(frozen=True, slots=True)
class Reservation:
    """One atomic reserve against a specific budget day.

    Reconciliation must go back to this exact day's key — a turn that crosses
    UTC midnight must refund the day that was charged, not the new one. The
    id keys the once-only reconciliation marker.
    """

    id: str
    day_key: str
    reserved_usd: float


class ChatBudgetExceeded(Exception):
    """The day's chat allocation is spent."""


class ChatBudgetUnavailable(Exception):
    """Budget accounting cannot run (missing rates or a Redis outage)."""


class ChatBudget(Protocol):
    """Budget seam; production is RedisChatBudget, tests script outcomes."""

    def reserve(self, amount_usd: float) -> Reservation: ...

    def reconcile(self, reservation: Reservation, actual_usd: float) -> None: ...


class RedisChatBudget:
    """UTC-daily allowance in Redis; reserve is atomic across processes."""

    def __init__(self, redis: Any, *, daily_limit_usd: float) -> None:
        self._redis = redis
        self._limit = daily_limit_usd

    def _key(self) -> str:
        return BUDGET_KEY_PREFIX + datetime.now(UTC).strftime("%Y%m%d")

    def reserve(self, amount_usd: float) -> Reservation:
        key = self._key()
        try:
            result = self._redis.eval(
                _RESERVE_LUA, 1, key, self._limit, amount_usd, _BUDGET_KEY_TTL_SECONDS
            )
        except Exception as exc:  # fail closed: no accounting, no paid processing
            raise ChatBudgetUnavailable(
                f"budget counter unavailable: {type(exc).__name__}"
            ) from exc
        if result == -1:
            raise ChatBudgetExceeded("daily chat budget exhausted")
        return Reservation(id=secrets.token_urlsafe(8), day_key=key, reserved_usd=amount_usd)

    def reconcile(self, reservation: Reservation, actual_usd: float) -> None:
        """Reconcile once against the reserved day's key.

        A SET-NX marker makes a repeated reconciliation a no-op so the refund
        can never be applied twice (e.g. a retried finalization path).
        """
        delta = actual_usd - reservation.reserved_usd
        marker = reservation.day_key + ":reconciled:" + reservation.id
        try:
            if not self._redis.set(marker, 1, nx=True, ex=_BUDGET_KEY_TTL_SECONDS):
                return
            self._redis.incrbyfloat(reservation.day_key, delta)
            self._redis.expire(reservation.day_key, _BUDGET_KEY_TTL_SECONDS)
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
    input_tokens = settings.chat_max_prompt_chars * 4 + _REQUEST_OVERHEAD_TOKENS
    per_call = (
        input_tokens * settings.chat_price_input_per_mtok
        + settings.chat_max_output_tokens * settings.chat_price_output_per_mtok
    ) / 1_000_000
    return per_call * _EXPECTED_MODEL_CALLS


def actual_usage_cost(settings: ChatSettings, usage_records: list[dict[str, Any]]) -> float:
    """Reconciled cost of a turn's model calls.

    Proxy-reported costs win when every call reported one; otherwise the
    configured rates price the observed tokens. Estimates and reported costs
    are distinguishable by the ``cost_usd`` field each record carries. Records
    for dispatches whose billed usage never arrived keep their conservative
    pre-dispatch estimate, so unknown usage is never refunded as zero.
    """
    input_rate = settings.chat_price_input_per_mtok or 0.0
    output_rate = settings.chat_price_output_per_mtok or 0.0
    return sum(
        float(record["cost_usd"])
        if record.get("cost_usd") is not None
        else (
            float(record.get("input_tokens", 0)) * input_rate
            + float(record.get("output_tokens", 0)) * output_rate
        )
        / 1_000_000
        for record in usage_records
    )


class ChatModelGateway:
    """The single dispatch point for every chat model call.

    Wraps the shared LLM client and bounds each purpose: output is capped at
    ``chat_max_output_tokens``, implicit SDK retries are disabled (one billed
    attempt per dispatch), total prompt size is hard-bounded, and usage is
    recorded per attempt. A conservative pre-dispatch estimate is recorded
    first and replaced by the billed usage when the call returns — a call that
    fails before reporting usage keeps the estimate, so reconciliation never
    treats unknown spend as zero.
    """

    def __init__(self, base: LLMClient, settings: ChatSettings) -> None:
        self._base = base
        self._settings = settings
        self.records: list[dict[str, Any]] = []

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
        max_completion_tokens: int | None = None,
        usage_sink: UsageSink | None = None,
    ) -> Any:
        if len(system) + len(prompt) > self._settings.chat_max_prompt_chars:
            raise TerminalError(
                f"chat prompt exceeds the configured bound "
                f"({self._settings.chat_max_prompt_chars} chars)"
            )

        resolved_model = model or self._settings.chat_model or self._base.model_for_purpose(purpose)
        if len(self.records) >= _EXPECTED_MODEL_CALLS:
            raise TerminalError("chat model-call allowance exhausted")
        input_tokens = self._settings.chat_max_prompt_chars * 4 + _REQUEST_OVERHEAD_TOKENS
        output_tokens = min(
            max_completion_tokens or self._settings.chat_max_output_tokens,
            self._settings.chat_max_output_tokens,
        )
        record: dict[str, Any] = {
            "purpose": purpose,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": None,
            "model": resolved_model,
        }
        self.records.append(record)

        def _sink(entry: dict[str, Any]) -> None:
            # Billed usage arrived: replace the conservative estimate in place.
            record.update(entry)
            if usage_sink is not None:
                usage_sink(entry)

        return self._base.structured(
            purpose=purpose,
            system=system,
            prompt=prompt,
            schema=schema,
            model=resolved_model,
            trace_content=trace_content,
            max_retries=0,
            max_completion_tokens=output_tokens,
            usage_sink=_sink,
        )


def new_turn_idempotency_token() -> str:
    """A server-side fallback request id (clients should send their own)."""
    return secrets.token_urlsafe(16)


__all__ = [
    "BUDGET_KEY_PREFIX",
    "ChatBudget",
    "ChatBudgetExceeded",
    "ChatBudgetUnavailable",
    "ChatModelGateway",
    "RedisChatBudget",
    "Reservation",
    "actual_usage_cost",
    "estimate_turn_reserve",
    "new_turn_idempotency_token",
]
