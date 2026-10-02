"""Shared fakes for the chat tests (no Qdrant, no DB, no network)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from app.retrieval.hybrid_search import RetrievalHit


def hit_for(article_number: str, *, category: str = "process", chunk: int = 0) -> RetrievalHit:
    return RetrievalHit.model_validate(
        {
            "score": 0.9,
            "article_id": f"{article_number}-v1.0",
            "article_number": article_number,
            "version": "1.0",
            "title": f"{article_number} title",
            "section": "General" if category == "process" else "Resolution",
            "chunk_index": chunk,
            "chunk_text": f"Body text of {article_number} chunk {chunk}.",
            "workflow_state": "published",
            "security_level": "restricted",
            "category": category,
            "service": "general" if category == "process" else None,
        }
    )


class FakeChatRetriever:
    """ChatRetriever stand-in returning scripted hits."""

    def __init__(self, hits: list[RetrievalHit] | None = None) -> None:
        self.hits = hits if hits is not None else [hit_for("KB0704")]
        self.calls: list[dict[str, Any]] = []

    def search(self, query: str, *, limit: int) -> list[RetrievalHit]:
        self.calls.append({"query": query, "limit": limit})
        return self.hits[:limit]


class FakeChatStore:
    """ChatStore stand-in recording every persistence call in order."""

    def __init__(self, history: list[dict[str, str]] | None = None) -> None:
        self._history = history or []
        self.events: list[tuple[str, dict[str, Any]]] = []

    def get_history(self, conversation_id: UUID, *, limit: int) -> list[dict[str, str]]:
        self.events.append(("get_history", {"limit": limit}))
        return list(self._history)[-limit:]

    def record_user_message(
        self,
        conversation_id: UUID,
        turn_id: UUID,
        *,
        content: str,
        blocked_layer: str | None,
    ) -> None:
        self.events.append(
            (
                "user_message",
                {"content": content, "blocked_layer": blocked_layer, "turn_id": turn_id},
            )
        )

    def record_assistant_message(
        self,
        conversation_id: UUID,
        turn_id: UUID,
        *,
        content: str,
        citations: list[dict[str, object]],
    ) -> None:
        self.events.append(
            ("assistant_message", {"content": content, "citations": citations, "turn_id": turn_id})
        )

    def complete_turn(
        self,
        turn_id: UUID,
        *,
        status: str,
        route: str | None,
        usage: dict[str, object] | None,
    ) -> None:
        self.events.append(
            (
                "complete_turn",
                {"status": status, "route": route, "usage": usage, "turn_id": turn_id},
            )
        )

    def fail_turn(self, turn_id: UUID, *, error_category: str) -> None:
        self.events.append(("fail_turn", {"error_category": error_category, "turn_id": turn_id}))

    def calls_named(self, name: str) -> list[dict[str, Any]]:
        return [payload for event, payload in self.events if event == name]


def new_ids() -> tuple[UUID, UUID]:
    return uuid4(), uuid4()
