"""Chat turn state flowing through the LangGraph chat workflow.

The graph is stateless between turns: each invocation starts from a state the
service builds (conversation history rehydrated from PostgreSQL), and every
terminal state is persisted by the ``persist_turn`` node. Nothing here is
checkpointed via the incident graph's ``WorkflowStateSaver``.
"""

from __future__ import annotations

from typing import Any, TypedDict

#: Serialized screening outcome attached to every turn.
SCREENING_KEY = "screening"


class ChatState(TypedDict, total=False):
    conversation_id: str
    turn_id: str
    operator_subject: str

    # screen_input
    user_message: str  # raw input; never persisted
    sanitized_message: str
    screening: dict[str, Any]  # {blocked, layer, reason, redaction_count}

    # load_context
    history: list[dict[str, str]]  # [{"role": "user"|"assistant", "content": ...}] oldest→newest
    history_summary: str  # rolling summary of messages older than the window

    # resolve_and_route
    route: str  # "knowledge" | "unavailable" | "blocked"
    route_reason: str
    unavailable_message: str
    search_query: str  # reference-resolved question actually sent to retrieval

    # retrieve_knowledge
    evidence: list[dict[str, Any]]

    # Service-provided usage summary persisted by persist_turn.
    usage_summary: dict[str, Any]

    # generate_answer / verify_answer
    answer_markdown: str
    cited_chunk_ids: list[str]
    sufficient_evidence: bool
    missing_evidence_note: str | None
    repair_count: int
    repair_pending: bool
    citations: list[dict[str, Any]]
    verification: dict[str, Any]


def evidence_key(item: dict[str, Any]) -> str:
    """The chunk id the model cites for one evidence item."""
    return str(item["chunk_id"])


__all__ = ["ChatState", "SCREENING_KEY", "evidence_key"]
