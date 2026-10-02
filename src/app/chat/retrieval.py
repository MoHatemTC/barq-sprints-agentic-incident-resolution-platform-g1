"""Full-KB retrieval for the admin chatbot.

This module deliberately does NOT reuse ``agent.retrieval.QdrantRetriever``:
that retriever hard-excludes ``category="process"`` (the manual articles are
hidden from the incident agent by design) and bundles resolution chunks for
incident diagnosis. The chatbot must search every permitted category, so it
calls the shared hybrid search directly with only the lifecycle and security
filters — the default workflow states (published, human_resolved) already
exclude retired and draft material.
"""

from __future__ import annotations

from typing import Protocol

from qdrant_client import QdrantClient

from app.chat.config import ChatSettings
from app.models.knowledge import SecurityLevel
from app.retrieval.embedding import EmbeddingEngine
from app.retrieval.filters import MetadataFilterBuilder
from app.retrieval.hybrid_search import RetrievalHit, timed_hybrid_search


class ChatRetriever(Protocol):
    """Retrieval seam so tests can substitute evidence without a Qdrant."""

    def search(self, query: str, *, limit: int) -> list[RetrievalHit]: ...


class QdrantChatRetriever:
    """Shared-collection retrieval with server-selected visibility only.

    No category or service restriction: process/general manual content sits in
    the same collection as canonical incident articles and both are in scope
    for chat questions.
    """

    def __init__(
        self,
        client: QdrantClient,
        *,
        engine: EmbeddingEngine | None = None,
        max_security_level: SecurityLevel = SecurityLevel.RESTRICTED,
    ) -> None:
        self._client = client
        self._engine = engine
        self._max_security_level = max_security_level

    def search(self, query: str, *, limit: int) -> list[RetrievalHit]:
        result = timed_hybrid_search(
            self._client,
            query,
            limit=limit,
            metadata=MetadataFilterBuilder(max_security_level=self._max_security_level),
            engine=self._engine,
        )
        return result.hits


def build_chat_retriever(settings: ChatSettings) -> QdrantChatRetriever:
    """Production retriever: shared Qdrant client, chat security ceiling."""
    from app.clients.qdrant import get_qdrant_client

    return QdrantChatRetriever(
        get_qdrant_client(),
        max_security_level=SecurityLevel(settings.chat_max_security_level),
    )


__all__ = ["ChatRetriever", "QdrantChatRetriever", "build_chat_retriever"]
