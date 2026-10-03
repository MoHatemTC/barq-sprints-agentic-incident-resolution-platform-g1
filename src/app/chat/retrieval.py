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

from typing import Any, Protocol

from qdrant_client import QdrantClient

from app.chat.cache import source_fingerprint
from app.chat.config import ChatSettings
from app.models.knowledge import KnowledgePayload, SecurityLevel
from app.retrieval.embedding import EmbeddingEngine
from app.retrieval.filters import SECURITY_LEVEL_ORDER, MetadataFilterBuilder
from app.retrieval.hybrid_search import RetrievalHit, timed_hybrid_search
from app.retrieval.ingest import build_point_id


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
        self._get_engine()
        result = timed_hybrid_search(
            self._client,
            query,
            limit=limit,
            metadata=MetadataFilterBuilder(max_security_level=self._max_security_level),
            engine=self._engine,
        )
        return result.hits

    def _get_engine(self) -> EmbeddingEngine:
        if self._engine is None:
            from agent.llm import get_embedding_engine

            self._engine = get_embedding_engine()
        return self._engine

    def embed_query(self, question: str) -> list[float]:
        return self._get_engine().embed_query(question).dense

    def validate_sources(self, sources: list[dict[str, Any]]) -> bool:
        """Fresh payload reads enforce lifecycle, visibility and content identity."""
        from app.core.config import get_retrieval_settings

        if not sources:
            return False
        point_ids = [
            build_point_id(item["article_id"], int(item["chunk_index"])) for item in sources
        ]
        points = self._client.retrieve(
            collection_name=get_retrieval_settings().qdrant_collection_name,
            ids=point_ids,
            with_payload=True,
            with_vectors=False,
        )
        payloads = {str(point.id): point.payload for point in points}
        ceiling = SECURITY_LEVEL_ORDER.index(self._max_security_level)
        for point_id, source in zip(point_ids, sources, strict=True):
            raw = payloads.get(point_id)
            if not raw:
                return False
            payload = KnowledgePayload.model_validate(raw)
            if payload.workflow_state.value not in ("published", "human_resolved"):
                return False
            if SECURITY_LEVEL_ORDER.index(payload.security_level) > ceiling:
                return False
            data = payload.model_dump(mode="json")
            data["article_id"] = payload.article_id
            if source_fingerprint(data) != source["fingerprint"]:
                return False
        return True


def build_chat_retriever(settings: ChatSettings) -> QdrantChatRetriever:
    """Production retriever: shared Qdrant client, chat security ceiling."""
    from app.clients.qdrant import get_qdrant_client

    return QdrantChatRetriever(
        get_qdrant_client(),
        max_security_level=SecurityLevel(settings.chat_max_security_level),
    )


__all__ = ["ChatRetriever", "QdrantChatRetriever", "build_chat_retriever"]
