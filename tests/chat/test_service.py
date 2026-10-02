"""Tests for ChatTurnService: budget gating, orchestration, failure handling."""

from __future__ import annotations

from agent.llm import TerminalError
from agent.prompts import PIIDetectionOutput
from app.chat.budget import ChatBudgetExceeded
from app.chat.config import ChatSettings
from app.chat.prompts import AnswerDraft, RouteDecision
from app.chat.service import ChatTurnService, TurnRequest
from observability.tracing import get_tracer
from tests.agent_support import FakeLLM
from tests.chat.support import FakeChatRetriever, FakeChatStore, new_ids

_SETTINGS = ChatSettings(
    _env_file=None,
    chat_price_input_per_mtok=0.5,
    chat_price_output_per_mtok=2.0,
    chat_daily_budget_usd=6.0,
)


class FakeBudget:
    """ChatBudget stand-in recording reserve/reconcile calls."""

    def __init__(self) -> None:
        self.reserves: list[float] = []
        self.reconciles: list[tuple[float, float]] = []
        self.fail_reserve = False

    def reserve(self, amount_usd: float) -> None:
        if self.fail_reserve:
            raise ChatBudgetExceeded("daily chat budget exhausted")
        self.reserves.append(amount_usd)

    def reconcile(self, actual_usd: float, reserved_usd: float) -> None:
        self.reconciles.append((actual_usd, reserved_usd))


def _answers() -> dict:
    return {
        "pii_detection": PIIDetectionOutput(findings=[]),
        "injection_classifier": _no_injection(),
        "chat_route": RouteDecision(request_type="knowledge", reason="t"),
        "chat_answer": AnswerDraft(
            answer_markdown="Answer from KB0704.",
            cited_chunk_ids=["KB0704-v1.0::chunk::0"],
            sufficient_evidence=True,
        ),
    }


def _no_injection():
    from agent.prompts import InjectionClassification

    return InjectionClassification(is_injection=False, reason="ok")


def _service(
    llm: FakeLLM,
    store: FakeChatStore,
    budget: FakeBudget,
) -> ChatTurnService:
    return ChatTurnService(
        llm=llm,  # type: ignore[arg-type]
        retriever=FakeChatRetriever(),  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
        budget=budget,  # type: ignore[arg-type]
        settings=_SETTINGS,
        tracer=get_tracer(),
    )


def _request() -> TurnRequest:
    conversation_id, turn_id = new_ids()
    return TurnRequest(
        conversation_id=conversation_id,
        turn_id=turn_id,
        operator_subject="barq-operator",
        user_message="Explain the known error register policy.",
    )


def test_successful_turn_reserves_and_reconciles() -> None:
    llm = FakeLLM(answers=_answers())
    store = FakeChatStore(history=[{"role": "user", "content": "earlier question"}])
    budget = FakeBudget()

    outcome = _service(llm, store, budget).handle_turn(_request())

    assert outcome.status == "succeeded"
    assert outcome.route == "knowledge"
    assert len(budget.reserves) == 1 and budget.reserves[0] > 0
    assert len(budget.reconciles) == 1
    completion = store.calls_named("attach_usage")[-1]
    usage = completion["usage"]
    assert usage["model_calls"] == 4
    assert usage["input_tokens"] == 3600  # FakeLLM records 900 in / 150 out per call
    assert usage["estimated_cost_usd"] > 0
    assert usage["cache_status"] == "none"


def test_budget_exhausted_blocks_without_any_model_call() -> None:
    llm = FakeLLM(answers={})
    store = FakeChatStore()
    budget = FakeBudget()
    budget.fail_reserve = True

    outcome = _service(llm, store, budget).handle_turn(_request())

    assert outcome.status == "blocked"
    assert llm.calls == []
    assert "budget" in store.calls_named("assistant_message")[0]["content"].lower()
    assert store.calls_named("complete_turn")[0]["usage"] == {"blocked_layer": "budget_exceeded"}
    assert budget.reserves == []


def test_budget_blocked_message_is_stored_as_placeholder_without_autotitle() -> None:
    """No full screening ran, so neither raw nor only-redacted text may persist."""
    llm = FakeLLM(answers={})
    store = FakeChatStore()
    budget = FakeBudget()
    budget.fail_reserve = True
    request = TurnRequest(
        conversation_id=_request().conversation_id,
        turn_id=_request().turn_id,
        operator_subject="barq-operator",
        user_message="My name is Jane Doe — what is a P1?",
    )

    _service(llm, store, budget).handle_turn(request)

    stored = store.calls_named("user_message")[0]
    assert stored["content"] == "[message withheld before screening]"
    assert stored["autotitle"] is False
    assert "Jane Doe" not in stored["content"]


def test_unconfigured_budget_refuses_paid_processing() -> None:
    llm = FakeLLM(answers={})
    store = FakeChatStore()
    service = ChatTurnService(
        llm=llm,  # type: ignore[arg-type]
        retriever=FakeChatRetriever(),  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
        budget=FakeBudget(),  # type: ignore[arg-type]
        settings=ChatSettings(_env_file=None),
        tracer=get_tracer(),
    )

    outcome = service.handle_turn(_request())

    assert outcome.status == "blocked"
    assert llm.calls == []
    assert "price rates" in store.calls_named("assistant_message")[0]["content"]


def test_model_failure_marks_turn_failed_and_still_reconciles() -> None:
    llm = FakeLLM(answers={**_answers(), "chat_answer": TerminalError("model call rejected")})
    store = FakeChatStore()
    budget = FakeBudget()

    outcome = _service(llm, store, budget).handle_turn(_request())

    assert outcome.status == "failed"
    assert outcome.error_category == "model_error"
    assert store.calls_named("fail_turn")[0]["error_category"] == "model_error"
    assert len(budget.reconciles) == 1


def test_unavailable_route_completes_blocked() -> None:
    answers = _answers()
    answers["chat_route"] = RouteDecision(request_type="work_note", reason="t")
    llm = FakeLLM(answers=answers)
    store = FakeChatStore()
    budget = FakeBudget()

    outcome = _service(llm, store, budget).handle_turn(_request())

    assert outcome.status == "blocked"
    assert outcome.route == "unavailable"


def test_older_history_is_summarized_and_persisted() -> None:
    from app.chat.prompts import HistorySummary

    llm = FakeLLM(
        answers={
            **_answers(),
            "chat_summarize": HistorySummary(summary="The operator asked what a KER is."),
        }
    )
    older = [
        {"role": "user", "content": "What is a KER?"},
        {"role": "assistant", "content": "A known error record."},
    ]
    store = FakeChatStore(unsummarized=older)
    budget = FakeBudget()

    outcome = _service(llm, store, budget).handle_turn(_request())

    assert outcome.status == "succeeded"
    assert [c["purpose"] for c in llm.calls].count("chat_summarize") == 1
    assert store.saved_summary is not None and store.saved_summary[1] == 2
    summarize_call = next(c for c in llm.calls if c["purpose"] == "chat_summarize")
    assert "known error record" in summarize_call["prompt"]


def test_summary_failure_never_blocks_the_turn() -> None:
    llm = FakeLLM(answers={**_answers(), "chat_summarize": TerminalError("model down")})
    store = FakeChatStore(unsummarized=[{"role": "user", "content": "What is a KER?"}])
    budget = FakeBudget()

    outcome = _service(llm, store, budget).handle_turn(_request())

    assert outcome.status == "succeeded"
    assert store.calls_named("publish_turn")[0]["status"] == "succeeded"


def test_no_older_history_skips_the_summarize_call() -> None:
    llm = FakeLLM(answers=_answers())
    store = FakeChatStore(history=[{"role": "user", "content": "earlier question"}])
    budget = FakeBudget()

    _service(llm, store, budget).handle_turn(_request())

    assert "chat_summarize" not in llm.purposes()
