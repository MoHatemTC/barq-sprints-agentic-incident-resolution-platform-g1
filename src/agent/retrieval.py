"""Evidence retrieval for the ``retrieve`` node (S2.5 ↔ S2.4 seam).

The node depends on the :class:`Retriever` protocol. The default implementation
uses the hybrid entry point on ``main`` (``app.retrieval.search.retrieve_knowledge``:
dense + BM25 fused with RRF, published-only, security-tier filtered).

**Why a second score.** RRF scores are rank sums: the top hit of *any* query scores
the same whether or not it is relevant, so they cannot answer "is there evidence at
all?" (manual §11.4 "Escalated — no evidence"). The retriever therefore also asks
Qdrant for the dense cosine similarity of the same chunks, under the same mandatory
filter, and the evidence gate (§11.7 ``threshold``) is applied to that. When S2.4's
reranker lands (#110), its calibrated score can replace the cosine without changing
this interface.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny

from agent.state import EvidenceItem, RetrievalResult
from app.clients.qdrant import DENSE_VECTOR_NAME
from app.models.knowledge import (
    CORPUS_CATEGORY_TO_CLASSIFICATION,
    Classification,
    SecurityLevel,
)
from app.retrieval.embedding import EmbeddedText, EmbeddingEngine
from app.retrieval.search import _build_filter, retrieve_knowledge
from app.workers.retry_policy import RetryableError, TerminalError

#: Classification label → corpus category (inverse of the S1.4 mapping, #70).
CLASSIFICATION_TO_CORPUS_CATEGORY: dict[Classification, str] = {
    label: category for category, label in CORPUS_CATEGORY_TO_CLASSIFICATION.items()
}


class Retriever(Protocol):
    def search(
        self,
        query: str,
        *,
        classification: Classification,
        top_k: int,
        threshold: float,
        incident_category: str | None = None,
    ) -> RetrievalResult: ...


def search_categories(classification: Classification, incident_category: str | None) -> list[str]:
    """Corpus categories to search.

    The model's label decides whether there can be evidence at all: ``security`` and
    ``other`` have no corpus article. Otherwise the incident's own ServiceNow category
    (already checked against the supported set) is searched too — a VPN failure that
    the model calls ``access`` is still filed under ``network``, where KB0001 lives
    (observed with Gemini on 2026-09-17).
    """
    mapped = CLASSIFICATION_TO_CORPUS_CATEGORY.get(classification)
    if mapped is None:
        return []
    categories = [mapped]
    corpus = set(CLASSIFICATION_TO_CORPUS_CATEGORY.values())
    if incident_category in corpus and incident_category not in categories:
        categories.append(incident_category)
    return categories


class _MemoEngine:
    """Embeds each query once even though two searches need it."""

    def __init__(self, inner: EmbeddingEngine) -> None:
        self._inner = inner
        self._cache: dict[str, EmbeddedText] = {}

    @property
    def dense_vector_size(self) -> int:
        return self._inner.dense_vector_size

    def embed_documents(self, texts: list[str]) -> list[EmbeddedText]:
        return self._inner.embed_documents(texts)

    def embed_query(self, text: str) -> EmbeddedText:
        if text not in self._cache:
            self._cache[text] = self._inner.embed_query(text)
        return self._cache[text]


class QdrantRetriever:
    def __init__(
        self,
        client_factory: Callable[[], QdrantClient],
        engine_factory: Callable[[], EmbeddingEngine],
        *,
        collection_name: str | None = None,
        max_security_level: SecurityLevel = SecurityLevel.INTERNAL,
    ) -> None:
        self._client_factory = client_factory
        self._engine_factory = engine_factory
        self._client: QdrantClient | None = None
        self._collection_name = collection_name
        self._max_security_level = max_security_level

    def _qdrant(self) -> QdrantClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def search(
        self,
        query: str,
        *,
        classification: Classification,
        top_k: int,
        threshold: float,
        incident_category: str | None = None,
    ) -> RetrievalResult:
        categories = search_categories(classification, incident_category)
        if not categories:
            # No corpus category covers this label (security, other). An unfiltered
            # search would still return the "nearest" article — measured at 0.58 for
            # a leave request — so there is, by definition, no evidence.
            return RetrievalResult(
                query=query,
                category_filter=None,
                hits=[],
                best_relevance=0.0,
                threshold=threshold,
                sufficient=False,
                latency_ms=0.0,
            )
        category = ",".join(categories)
        extra = Filter(must=[FieldCondition(key="category", match=MatchAny(any=categories))])
        engine = _MemoEngine(self._engine_factory())
        started = time.perf_counter()
        try:
            client = self._qdrant()
            hits = retrieve_knowledge(
                client,
                query,
                collection_name=self._collection_name,
                limit=top_k,
                extra_filter=extra,
                max_security_level=self._max_security_level,
                engine=engine,
            )
            relevance = self._dense_scores(client, engine.embed_query(query), extra, top_k)
        except ValueError as exc:
            # Malformed payloads violate the ingestion contract: retrying cannot help.
            raise TerminalError(f"retrieval contract violated: {exc}") from exc
        except Exception as exc:
            # Qdrant unreachable / timed out: the next attempt may succeed.
            raise RetryableError(f"retrieval unavailable: {type(exc).__name__}") from exc
        latency_ms = (time.perf_counter() - started) * 1000

        items = [
            EvidenceItem(
                article_id=hit.article_id,
                article_number=hit.article_number,
                version=hit.version,
                title=hit.title,
                section=hit.section,
                chunk_index=hit.chunk_index,
                text=hit.chunk_text,
                fused_score=hit.score,
                relevance=relevance.get((hit.article_id, hit.chunk_index), 0.0),
            )
            for hit in hits
        ]
        best = max((item.relevance for item in items), default=0.0)
        return RetrievalResult(
            query=query,
            category_filter=category,
            hits=items,
            best_relevance=best,
            threshold=threshold,
            sufficient=bool(items) and best >= threshold,
            latency_ms=round(latency_ms, 2),
        )

    def _dense_scores(
        self, client: QdrantClient, embedded: EmbeddedText, extra: Filter, top_k: int
    ) -> dict[tuple[str, int], float]:
        from app.retrieval.search import _get_default_collection_name

        response = client.query_points(
            collection_name=self._collection_name or _get_default_collection_name(),
            query=embedded.dense,
            using=DENSE_VECTOR_NAME,
            query_filter=_build_filter(extra, self._max_security_level),
            limit=max(top_k * 4, 20),
            with_payload=["article_number", "version", "chunk_index"],
        )
        scores: dict[tuple[str, int], float] = {}
        for point in response.points:
            payload: dict[str, Any] = point.payload or {}
            key = (
                f"{payload.get('article_number')}-v{payload.get('version')}",
                int(payload.get("chunk_index", -1)),
            )
            scores[key] = max(0.0, min(1.0, float(point.score)))
        return scores


def build_default_retriever() -> Retriever:
    from agent.config import get_agent_settings
    from agent.llm import get_embedding_engine
    from app.clients.qdrant import get_qdrant_client

    return QdrantRetriever(
        get_qdrant_client,
        get_embedding_engine,
        max_security_level=SecurityLevel(get_agent_settings().agent_max_security_level),
    )


__all__ = [
    "CLASSIFICATION_TO_CORPUS_CATEGORY",
    "QdrantRetriever",
    "Retriever",
    "build_default_retriever",
    "search_categories",
]
