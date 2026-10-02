"""Turn orchestration: budget gate → chat graph → reconcile.

The service is synchronous and runs on a worker thread (the model client and
Qdrant search are sync). The router has already claimed the turn row and
checked ownership before calling :meth:`ChatTurnService.handle_turn`; this
module owns everything from the first model-bound risk (cost) to the final
persisted status.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog

from agent.llm import (
    InvalidModelOutputError,
    LLMClient,
    ModelRefusalError,
    RetryableError,
    TerminalError,
)
from app.chat.budget import (
    ChatBudget,
    ChatBudgetExceeded,
    ChatBudgetUnavailable,
    UsageRecordingLLM,
    actual_usage_cost,
    estimate_turn_reserve,
)
from app.chat.config import ChatSettings
from app.chat.graph import build_chat_graph
from app.chat.nodes import ChatGraphDeps
from app.chat.retrieval import ChatRetriever
from app.chat.state import ChatState
from app.chat.store import ChatStore
from observability.redaction import redact_text
from observability.tracing import Tracer

logger = structlog.get_logger(__name__)

#: Most recent exchanges rehydrated as conversation context (M2 makes the
#: history budget configurable and adds summarization).
HISTORY_MESSAGE_LIMIT = 6

_RESERVE_BUDGET_REFUSAL = (
    "The daily chat budget is exhausted. Chat answers resume after the 00:00 UTC reset."
)
_UNCONFIGURED_BUDGET_REFUSAL = (
    "Chat cost accounting is not configured (verified price rates missing), so paid "
    "processing is refused. Ask an administrator to set CHAT_PRICE_INPUT_PER_MTOKEN and "
    "CHAT_PRICE_OUTPUT_PER_MTOKEN."
)


@dataclass(frozen=True, slots=True)
class TurnRequest:
    conversation_id: UUID
    turn_id: UUID
    operator_subject: str
    user_message: str


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    status: str
    route: str | None
    error_category: str | None


class ChatTurnService:
    """Runs one chat turn: reserve cost, execute the graph, reconcile, persist."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        retriever: ChatRetriever,
        store: ChatStore,
        budget: ChatBudget,
        settings: ChatSettings,
        tracer: Tracer,
    ) -> None:
        self._llm = llm
        self._retriever = retriever
        self._store = store
        self._budget = budget
        self._settings = settings
        self._tracer = tracer

    def handle_turn(self, request: TurnRequest) -> TurnOutcome:
        if not self._settings.budget_configured:
            return self._blocked_turn(
                request,
                _UNCONFIGURED_BUDGET_REFUSAL,
                usage={"blocked_layer": "budget_unconfigured"},
            )

        usage_records: list[dict[str, Any]] = []
        try:
            reserve = estimate_turn_reserve(self._settings, len(request.user_message))
            self._budget.reserve(reserve)
        except ChatBudgetExceeded:
            return self._blocked_turn(
                request, _RESERVE_BUDGET_REFUSAL, usage={"blocked_layer": "budget_exceeded"}
            )
        except ChatBudgetUnavailable as exc:
            logger.warning("chat_budget_unavailable", error=type(exc).__name__)
            return self._blocked_turn(
                request, _UNCONFIGURED_BUDGET_REFUSAL, usage={"blocked_layer": "budget_unavailable"}
            )

        state: ChatState = {
            "conversation_id": str(request.conversation_id),
            "turn_id": str(request.turn_id),
            "operator_subject": request.operator_subject,
            "user_message": request.user_message,
            "history": self._store.get_history(
                request.conversation_id, limit=HISTORY_MESSAGE_LIMIT
            ),
        }
        graph = build_chat_graph(
            ChatGraphDeps(
                llm=UsageRecordingLLM(self._llm, usage_records),
                retriever=self._retriever,
                store=self._store,
                settings=self._settings,
                tracer=self._tracer,
            )
        )
        try:
            final = graph.invoke(state, config={"recursion_limit": 12})
        except Exception as exc:
            self._reconcile(usage_records, reserve)
            category = _error_category(exc)
            logger.warning(
                "chat_turn_failed", turn_id=str(request.turn_id), error_category=category
            )
            self._store.fail_turn(request.turn_id, error_category=category)
            return TurnOutcome(status="failed", route=None, error_category=category)

        self._reconcile(usage_records, reserve)
        # persist_turn already closed the row without usage (so a crash between
        # graph and reconcile cannot leave the turn 'running'); this second
        # write attaches the reconciled usage summary.
        self._store.complete_turn(
            request.turn_id,
            status="succeeded" if final.get("route") == "knowledge" else "blocked",
            route=final.get("route"),
            usage=_usage_summary(self._settings, usage_records),
        )
        return TurnOutcome(
            status="succeeded" if final.get("route") == "knowledge" else "blocked",
            route=final.get("route"),
            error_category=None,
        )

    def _blocked_turn(
        self, request: TurnRequest, refusal: str, *, usage: dict[str, object]
    ) -> TurnOutcome:
        """Close a turn that never reached the answering model.

        The user message is stored only under deterministic redaction — no
        model-bound screening has run at this point.
        """
        self._store.record_user_message(
            request.conversation_id,
            request.turn_id,
            content=redact_text(request.user_message),
            blocked_layer=None,
        )
        self._store.record_assistant_message(
            request.conversation_id, request.turn_id, content=refusal, citations=[]
        )
        self._store.complete_turn(request.turn_id, status="blocked", route=None, usage=usage)
        return TurnOutcome(status="blocked", route=None, error_category=None)

    def _reconcile(self, usage_records: list[dict[str, Any]], reserve: float) -> None:
        actual = actual_usage_cost(self._settings, usage_records)
        self._budget.reconcile(actual, reserve)


def _usage_summary(settings: ChatSettings, usage_records: list[dict[str, Any]]) -> dict[str, Any]:
    reported = [float(r["cost_usd"]) for r in usage_records if r.get("cost_usd") is not None]
    return {
        "model_calls": len(usage_records),
        "input_tokens": sum(int(r.get("input_tokens", 0)) for r in usage_records),
        "output_tokens": sum(int(r.get("output_tokens", 0)) for r in usage_records),
        "estimated_cost_usd": actual_usage_cost(settings, usage_records),
        "reported_cost_usd": sum(reported)
        if reported and len(reported) == len(usage_records)
        else None,
        "cache_status": "none",
    }


def _error_category(exc: Exception) -> str:
    if isinstance(exc, (RetryableError, TerminalError, InvalidModelOutputError, ModelRefusalError)):
        return "model_error"
    return "internal_error"


__all__ = [
    "HISTORY_MESSAGE_LIMIT",
    "ChatTurnService",
    "TurnOutcome",
    "TurnRequest",
]
