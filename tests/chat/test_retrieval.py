"""Tests for the chat retriever's filter construction.

The critical contract: the chat filter must NOT carry the incident agent's
``category != 'process'`` must-not, so manual articles stay reachable, while
workflow-state and security filters stay in force.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from app.chat.retrieval import QdrantChatRetriever
from app.models.knowledge import SecurityLevel
from app.retrieval.embedding import EmbeddedText


class _FakeEmbeddingEngine:
    def embed_documents(self, texts: list[str]) -> list[EmbeddedText]:
        return [EmbeddedText(dense=[0.0], sparse_indices=[0], sparse_values=[1.0]) for _ in texts]

    def embed_query(self, text: str) -> EmbeddedText:
        return EmbeddedText(dense=[0.1, 0.2], sparse_indices=[1], sparse_values=[0.5])


class _FakeQdrant:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def query_points(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)

        class _Point:
            id = "p"

            payload = {
                "article_number": "KB0704",
                "version": "1.0",
                "title": "Known error register",
                "sys_id": "sys",
                "category": "process",
                "service": "general",
                "workflow_state": "published",
                "security_level": "restricted",
                "section": "General",
                "chunk_index": 0,
                "total_chunks": 1,
                "chunk_text": "Link the incident to the known error record.",
            }

        response = MagicMock()
        response.points = [_Point()]
        return response


def test_chat_retrieval_searches_every_category_including_process() -> None:
    client = _FakeQdrant()
    retriever = QdrantChatRetriever(
        client,  # type: ignore[arg-type]
        engine=_FakeEmbeddingEngine(),
        max_security_level=SecurityLevel.RESTRICTED,
    )

    hits = retriever.search("known error register", limit=5)

    assert len(hits) == 1
    assert hits[0].article_number == "KB0704"
    call = client.calls[0]
    must = call["query_filter"].must
    keys = {condition.key for condition in must}
    # workflow_state + security only: no category condition of any kind, so the
    # process/manual articles remain searchable (unlike the incident retriever).
    assert keys == {"workflow_state", "security_level"}
    assert call["limit"] == 5


def test_chat_retrieval_security_ceiling_is_server_selected() -> None:
    client = _FakeQdrant()
    retriever = QdrantChatRetriever(
        client,  # type: ignore[arg-type]
        engine=_FakeEmbeddingEngine(),
        max_security_level=SecurityLevel.INTERNAL,
    )

    retriever.search("anything", limit=2)

    security_condition = next(
        c for c in client.calls[0]["query_filter"].must if c.key == "security_level"
    )
    assert security_condition.match.any == ["public", "internal"]
