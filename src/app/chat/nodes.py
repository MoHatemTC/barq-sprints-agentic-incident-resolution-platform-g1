"""Chat graph nodes.

Each node is a small sync function over ``ChatState``. Model-bound work goes
through the ``LLMClient`` protocol; persistence goes through the ``ChatStore``
port. Citations are validated in code — a model-named chunk id that is not in
the retrieved evidence never reaches the persisted answer without one bounded
repair attempt first.
"""

from __future__ import annotations

from typing import Any

import structlog

from agent.llm import LLMClient
from app.chat.citations import Citation, build_citation, chunk_identity
from app.chat.config import ChatSettings
from app.chat.prompts import (
    CHAT_ANSWER_SYSTEM,
    CHAT_ROUTE_SYSTEM,
    AnswerDraft,
    RouteDecision,
    chat_answer_prompt,
    chat_route_prompt,
)
from app.chat.retrieval import ChatRetriever
from app.chat.screening import screen_chat_input
from app.chat.state import ChatState, evidence_key
from observability.redaction import REDACTED
from observability.tracing import Tracer

logger = structlog.get_logger(__name__)

#: One answer-repair attempt after failed citation verification (no unbounded loop).
MAX_REPAIRS = 1

#: Hard cap on one screened chat message.
SCREENING_CHAR_LIMIT = 6000

#: Display cap for a persisted citation excerpt; full text stays in Qdrant.
_EXCERPT_MAX_CHARS = 600

_UNAVAILABLE_MESSAGES = {
    "incident_read": (
        "Searching and reading live ServiceNow incidents is not available in this "
        "release yet. You can still ask knowledge-base questions here."
    ),
    "work_note": (
        "Drafting and posting work notes is not available in this release yet. "
        "You can still ask knowledge-base questions here."
    ),
}


def screen_input(state: ChatState, deps: ChatGraphDeps) -> dict[str, Any]:
    """Screen the raw user message; persist its sanitized form or the refusal."""
    with deps.tracer.span(
        "guardrail.chat_input",
        as_type="guardrail",
        input={"stage": "chat_input_guardrail", "turn_id": state.get("turn_id")},
    ):
        screening = screen_chat_input(
            deps.llm, state["user_message"], max_chars=SCREENING_CHAR_LIMIT
        )
    if screening.blocked:
        deps.store.record_user_message(
            state["conversation_id"],
            state["turn_id"],
            content=REDACTED,
            blocked_layer=screening.layer,
            autotitle=False,
        )
    else:
        deps.store.record_user_message(
            state["conversation_id"],
            state["turn_id"],
            content=screening.sanitized_text,
            blocked_layer=None,
        )
    return {
        "sanitized_message": screening.sanitized_text,
        "screening": {
            "blocked": screening.blocked,
            "layer": screening.layer,
            "reason": screening.refusal,
            "redaction_count": screening.redaction_count,
        },
    }


def route(state: ChatState, deps: ChatGraphDeps) -> dict[str, Any]:
    decision = deps.llm.structured(
        purpose="chat_route",
        system=CHAT_ROUTE_SYSTEM,
        prompt=chat_route_prompt(
            state["sanitized_message"],
            state.get("history", []),
            summary=state.get("history_summary") or None,
        ),
        schema=RouteDecision,
        model=deps.settings.chat_model,
        max_completion_tokens=deps.settings.chat_max_output_tokens,
    )
    if decision.request_type == "knowledge":
        # Reference resolution: the rewritten, self-contained question drives
        # retrieval; a topic change or standalone question keeps the raw text.
        search_query = (decision.search_question or "").strip()
        return {
            "route": "knowledge",
            "route_reason": decision.reason,
            "search_query": search_query or state["sanitized_message"],
        }
    return {
        "route": "unavailable",
        "route_reason": decision.reason,
        "unavailable_message": _UNAVAILABLE_MESSAGES[decision.request_type],
    }


def retrieve_knowledge(state: ChatState, deps: ChatGraphDeps) -> dict[str, Any]:
    query = state.get("search_query") or state["sanitized_message"]
    hits = deps.retriever.search(query, limit=deps.settings.chat_evidence_chunk_limit)
    evidence = []
    for hit in hits:
        citation = build_citation(hit)
        evidence.append(
            {
                "chunk_id": chunk_identity(citation.article_id, citation.chunk_index),
                "article_number": citation.article_number,
                "version": citation.version,
                "title": citation.title,
                "section": citation.section,
                "chunk_index": citation.chunk_index,
                "manual_section": citation.manual_section,
                "chunk_text": hit.chunk_text,
            }
        )
    return {"evidence": evidence}


def generate_answer(state: ChatState, deps: ChatGraphDeps) -> dict[str, Any]:
    draft = deps.llm.structured(
        purpose="chat_answer",
        system=CHAT_ANSWER_SYSTEM,
        prompt=chat_answer_prompt(
            state.get("search_query") or state["sanitized_message"],
            state.get("evidence", []),
            state.get("history", []),
            summary=state.get("history_summary") or None,
        ),
        schema=AnswerDraft,
        model=deps.settings.chat_model,
        max_completion_tokens=deps.settings.chat_max_output_tokens,
    )
    return {
        "answer_markdown": draft.answer_markdown,
        "cited_chunk_ids": list(draft.cited_chunk_ids),
        "sufficient_evidence": draft.sufficient_evidence,
        "missing_evidence_note": draft.missing_evidence_note,
    }


#: Shown when no draft survives citation verification; never publish an answer
#: whose citations could not be checked against the retrieved evidence.
_EVIDENCE_GAP_FALLBACK = (
    "I found reference material but could not verify that the draft answer is "
    "supported by it, so I am not publishing the answer. Please rephrase the "
    "question or try again."
)


def verify_answer(state: ChatState, deps: ChatGraphDeps) -> dict[str, Any]:
    """Code-only citation validation; one bounded repair on unknown chunk ids.

    Fail-closed: a draft that still names evidence it was not shown — or that
    cites nothing at all — is replaced by an explicit evidence-gap response.
    """
    cited = state.get("cited_chunk_ids", [])
    valid_ids = {evidence_key(item) for item in state.get("evidence", [])}
    invalid = [chunk_id for chunk_id in cited if chunk_id not in valid_ids]
    if invalid and state.get("repair_count", 0) < MAX_REPAIRS:
        logger.info(
            "chat_answer_repair", invalid_citations=len(invalid), turn_id=state.get("turn_id")
        )
        return {"repair_count": state.get("repair_count", 0) + 1, "repair_pending": True}

    citations = [
        _citation_for(chunk_id, state.get("evidence", []))
        for chunk_id in dict.fromkeys(cited)
        if chunk_id in valid_ids
    ]
    passed = not invalid and bool(citations)
    if passed:
        return {
            "repair_pending": False,
            "verification": {"passed": True, "invalid_citations": []},
            "citations": [citation.model_dump(mode="json") for citation in citations],
        }

    note = str(state.get("missing_evidence_note") or "").strip()
    gap_answer = f"{_EVIDENCE_GAP_FALLBACK}\n\n{note}" if note else _EVIDENCE_GAP_FALLBACK
    logger.warning(
        "chat_answer_unverified",
        invalid_citations=invalid,
        cited_count=len(cited),
        turn_id=state.get("turn_id"),
    )
    return {
        "repair_pending": False,
        "answer_markdown": gap_answer,
        "citations": [],
        "verification": {"passed": False, "invalid_citations": invalid},
    }


def unavailable_notice(state: ChatState, deps: ChatGraphDeps) -> dict[str, Any]:
    return {
        "answer_markdown": state.get(
            "unavailable_message",
            "This capability is not available in this release yet.",
        ),
        "citations": [],
    }


def persist_turn(state: ChatState, deps: ChatGraphDeps) -> dict[str, Any]:
    """Record the assistant message and close the turn (the graph's only writes)."""
    screening = state.get("screening", {})
    if screening.get("blocked"):
        deps.store.publish_turn(
            state["conversation_id"],
            state["turn_id"],
            content=str(screening.get("reason") or "This message was withheld."),
            citations=[],
            status="blocked",
            route=None,
            usage={"blocked_layer": screening.get("layer")},
        )
        return {"route": "blocked"}

    verification_passed = (state.get("verification") or {}).get("passed", True)
    status = "succeeded" if state.get("route") == "knowledge" and verification_passed else "blocked"
    deps.store.publish_turn(
        state["conversation_id"],
        state["turn_id"],
        content=state["answer_markdown"],
        citations=list(state.get("citations", [])),
        status=status,
        route=state.get("route"),
        usage=state.get("usage_summary"),
    )
    return {}


class ChatGraphDeps:
    """Typed dependency seam for the chat nodes (fakes substitute each field)."""

    def __init__(
        self,
        llm: LLMClient,
        retriever: ChatRetriever,
        store: Any,
        settings: ChatSettings,
        tracer: Tracer,
    ) -> None:
        self.llm = llm
        self.retriever = retriever
        self.store = store
        self.settings = settings
        self.tracer = tracer


def _citation_for(chunk_id: str, evidence: list[dict[str, Any]]) -> Citation:
    item = next(item for item in evidence if item["chunk_id"] == chunk_id)
    return Citation(
        article_id=item["chunk_id"].split("::chunk::")[0],
        article_number=item["article_number"],
        version=item["version"],
        title=item["title"],
        section=item["section"],
        chunk_index=int(item["chunk_index"]),
        manual_section=item.get("manual_section"),
        excerpt=str(item["chunk_text"])[:_EXCERPT_MAX_CHARS],
        article_url=None,
    )


__all__ = [
    "ChatGraphDeps",
    "MAX_REPAIRS",
    "generate_answer",
    "persist_turn",
    "retrieve_knowledge",
    "route",
    "screen_input",
    "unavailable_notice",
    "verify_answer",
]
