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
    ChatModelGateway,
    Reservation,
    actual_usage_cost,
    estimate_turn_reserve,
)
from app.chat.cache import RedisAnswerCache
from app.chat.config import ChatSettings
from app.chat.graph import build_chat_graph
from app.chat.nodes import ChatGraphDeps
from app.chat.prompts import (
    CHAT_SUMMARIZE_SYSTEM,
    HISTORY_SUMMARY_CHARS,
    HistorySummary,
    chat_summarize_prompt,
)
from app.chat.retrieval import ChatRetriever
from app.chat.state import ChatState
from app.chat.store import ChatStore
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

#: Stored for messages that never reached full screening (budget-blocked
#: turns); the raw text is discarded, not merely redacted.
_WITHHELD_MESSAGE_PLACEHOLDER = "[message withheld before screening]"


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
        cache: RedisAnswerCache | None = None,
        cache_scope: str = "",
    ) -> None:
        self._llm = llm
        self._retriever = retriever
        self._store = store
        self._budget = budget
        self._settings = settings
        self._tracer = tracer
        self._cache = cache
        self._cache_scope = cache_scope

    def handle_turn(self, request: TurnRequest) -> TurnOutcome:
        if not self._settings.budget_configured:
            return self._blocked_turn(
                request,
                _UNCONFIGURED_BUDGET_REFUSAL,
                usage={"blocked_layer": "budget_unconfigured"},
            )

        try:
            reservation = self._budget.reserve(
                estimate_turn_reserve(self._settings, len(request.user_message))
            )
        except ChatBudgetExceeded:
            return self._blocked_turn(
                request, _RESERVE_BUDGET_REFUSAL, usage={"blocked_layer": "budget_exceeded"}
            )
        except ChatBudgetUnavailable as exc:
            logger.warning("chat_budget_unavailable", error=type(exc).__name__)
            return self._blocked_turn(
                request, _UNCONFIGURED_BUDGET_REFUSAL, usage={"blocked_layer": "budget_unavailable"}
            )

        # Everything from the first context read to the final graph state is
        # one lifecycle: any failure reconciles the reservation and closes the
        # turn — a storage outage must never leave a running turn or a live
        # reservation behind.
        gateway = ChatModelGateway(self._llm, self._settings)
        try:
            history = self._store.get_history(
                request.conversation_id, limit=self._settings.chat_history_message_limit
            )
            summary = self._refresh_summary(request.conversation_id, history, gateway)
            state: ChatState = {
                "conversation_id": str(request.conversation_id),
                "turn_id": str(request.turn_id),
                "operator_subject": request.operator_subject,
                "user_message": request.user_message,
                "history": history,
                "history_summary": summary,
            }
            final = build_chat_graph(
                ChatGraphDeps(
                    llm=gateway,
                    retriever=self._retriever,
                    store=self._store,
                    settings=self._settings,
                    tracer=self._tracer,
                    cache=self._cache,
                    cache_scope=self._cache_scope + ":" + request.operator_subject,
                )
            ).invoke(state, config={"recursion_limit": 12})
        except Exception as exc:
            self._reconcile(reservation, gateway.records)
            category = _error_category(exc)
            logger.warning(
                "chat_turn_failed", turn_id=str(request.turn_id), error_category=category
            )
            self._store.fail_turn(request.turn_id, error_category=category)
            return TurnOutcome(status="failed", route=None, error_category=category)

        self._reconcile(reservation, gateway.records)
        # The graph's publish_turn owns route/status; this guarded write only
        # attaches the reconciled usage summary and never changes the outcome.
        self._store.attach_usage(
            request.turn_id,
            usage=_usage_summary(
                self._settings, gateway.records, final.get("cache_status", "none")
            ),
        )
        published = bool(final.get("published"))
        return TurnOutcome(
            status=str(final.get("terminal_status") or "failed"),
            route=final.get("route") if published else None,
            error_category=None if published else "stale_reclaimed",
        )

    def _refresh_summary(
        self,
        conversation_id: UUID,
        history: list[dict[str, str]],
        llm: ChatModelGateway,
    ) -> str:
        """Extend the persisted rolling summary with messages that aged out.

        One bounded model call per turn, made only when messages older than
        the recent window are not covered by the stored summary yet. A blank
        or failed summary leaves the cursor untouched — unsummarized messages
        are never silently marked as processed. Memory is best-effort: any
        failure falls back to the summary already loaded for this turn.
        """
        summary = ""
        try:
            summary, summary_seq = self._store.get_summary(conversation_id)
            # Messages omitted by the memory budget must enter the summary too,
            # even when they are still among the last six database messages.
            recent_budget = max(
                0,
                self._settings.chat_memory_budget_chars
                - min(HISTORY_SUMMARY_CHARS, self._settings.chat_memory_budget_chars // 4)
                - 64,
            )
            used = 0
            retained = 0
            for message in reversed(history):
                size = len(message["content"]) + len(message["role"]) + 3
                if retained and used + size > recent_budget:
                    break
                retained += 1
                used += size
            older = self._store.unsummarized_messages(
                conversation_id,
                after_seq=summary_seq,
                history_limit=max(1, retained),
                batch=self._settings.chat_memory_budget_chars,
            )
            if not older:
                return summary
            prompt = chat_summarize_prompt(summary, older)
            result = llm.structured(
                purpose="chat_summarize",
                system=CHAT_SUMMARIZE_SYSTEM,
                prompt=prompt,
                schema=HistorySummary,
                model=self._settings.chat_model,
                max_completion_tokens=self._settings.chat_max_output_tokens,
            )
            new_summary = result.summary.strip()
            if not new_summary:
                logger.warning("chat_summary_blank_output", turn_conversation=str(conversation_id))
                return summary
            self._store.save_summary(
                conversation_id, summary=new_summary, through_seq=int(older[-1]["seq"])
            )
            return new_summary
        except Exception as exc:  # noqa: BLE001 — memory is best-effort context
            logger.warning("chat_summary_refresh_failed", error=type(exc).__name__)
            return summary

    def _blocked_turn(
        self, request: TurnRequest, refusal: str, *, usage: dict[str, object]
    ) -> TurnOutcome:
        """Close a turn that never reached the answering model.

        Full screening has not run at this point, so the user message is stored
        as a withheld placeholder — never raw or only-redacted text — and must
        not name the conversation (no auto-title from unscreened content).
        """
        self._store.record_user_message(
            request.conversation_id,
            request.turn_id,
            content=_WITHHELD_MESSAGE_PLACEHOLDER,
            blocked_layer=None,
            autotitle=False,
        )
        self._store.record_assistant_message(
            request.conversation_id, request.turn_id, content=refusal, citations=[]
        )
        self._store.complete_turn(request.turn_id, status="blocked", route=None, usage=usage)
        return TurnOutcome(status="blocked", route=None, error_category=None)

    def _reconcile(self, reservation: Reservation, usage_records: list[dict[str, Any]]) -> None:
        actual = actual_usage_cost(self._settings, usage_records)
        self._budget.reconcile(reservation, actual)


def _usage_summary(
    settings: ChatSettings, usage_records: list[dict[str, Any]], cache_status: str = "none"
) -> dict[str, Any]:
    reported = [float(r["cost_usd"]) for r in usage_records if r.get("cost_usd") is not None]
    return {
        "model_calls": len(usage_records),
        "input_tokens": sum(int(r.get("input_tokens", 0)) for r in usage_records),
        "output_tokens": sum(int(r.get("output_tokens", 0)) for r in usage_records),
        "estimated_cost_usd": actual_usage_cost(settings, usage_records),
        "reported_cost_usd": sum(reported)
        if reported and len(reported) == len(usage_records)
        else None,
        "cache_status": cache_status,
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
