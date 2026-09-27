from __future__ import annotations

import time
from dataclasses import dataclass

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, Fusion, FusionQuery, Prefetch, SparseVector

from app.clients.qdrant import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME
from app.core.config import RetrievalMode, get_retrieval_settings
from app.models.knowledge import KnowledgePayload
from app.retrieval.embedding import EmbeddingEngine, FastEmbedEngine
from app.retrieval.filters import MetadataFilterBuilder, build_metadata_filter

logger = structlog.get_logger(__name__)

DEFAULT_COLLECTION_NAME = "incident_knowledge_base"

# Multiplier for the number of candidates to fetch from Qdrant before reranking.
DEFAULT_RERANK_CANDIDATE_MULTIPLIER = 4


class RetrievalHit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    score: float
    article_id: str  # Composed unique identifier, e.g. "KB0010-v2.0"
    article_number: str  # Article base identifier, e.g. "KB0010"
    version: str  # Semantic version, e.g. "2.0"
    title: str  # Article title
    section: str  # Section heading the chunk belongs to
    chunk_index: int  # 0-indexed position within article
    chunk_text: str  # Chunk content text
    workflow_state: str  # Always "published" for hits returned by retrieve_knowledge
    security_level: str  # Audience tier: "public", "internal" or "restricted"
    category: str  # Knowledge category, e.g. "database", "network"
    service: str | None = None  # Affected service name, e.g. "postgres", "redis"


@dataclass(frozen=True)
class SearchResult:
    hits: list[RetrievalHit]
    latency_ms: float
    mode: RetrievalMode


def _get_default_collection_name() -> str:
    from app.core.config import get_retrieval_settings

    return get_retrieval_settings().qdrant_collection_name


def _validate_hit(point) -> RetrievalHit:
    payload = point.payload
    if payload is None:
        raise ValueError(f"Point {point.id} returned without payload")
    try:
        validated = KnowledgePayload.model_validate(payload)
    except (ValidationError, TypeError, ValueError) as err:
        raise ValueError(
            f"Point {point.id} has malformed payload violating the ingestion contract: {err}"
        ) from err
    return RetrievalHit(
        score=float(point.score),
        article_id=validated.article_id,
        article_number=validated.article_number,
        version=validated.version,
        title=validated.title,
        section=validated.section,
        chunk_index=validated.chunk_index,
        chunk_text=validated.chunk_text,
        workflow_state=validated.workflow_state.value,
        security_level=validated.security_level.value,
        category=validated.category,
        service=validated.service,
    )


def _tie_break_sort(hits: list[RetrievalHit]) -> list[RetrievalHit]:
    """Deterministic sort: descending score, ties broken by (article_id, chunk_index)."""
    return sorted(hits, key=lambda h: (-h.score, h.article_id, h.chunk_index))


def hybrid_search(
    client: QdrantClient,
    query: str,
    *,
    collection_name: str | None = None,
    limit: int = 5,
    metadata: MetadataFilterBuilder | None = None,
    extra_filter: Filter | None = None,
    engine: EmbeddingEngine | None = None,
    mode: RetrievalMode | None = None,
) -> list[RetrievalHit]:
    result = timed_hybrid_search(
        client,
        query,
        collection_name=collection_name,
        limit=limit,
        metadata=metadata,
        extra_filter=extra_filter,
        engine=engine,
        mode=mode,
    )
    return result.hits


def timed_hybrid_search(
    client: QdrantClient,
    query: str,
    *,
    collection_name: str | None = None,
    limit: int = 5,
    metadata: MetadataFilterBuilder | None = None,
    extra_filter: Filter | None = None,
    engine: EmbeddingEngine | None = None,
    mode: RetrievalMode | None = None,
):
    """
    Same as hybrid_search() but returns a SearchResult with latency_ms and mode.
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")

    settings = get_retrieval_settings()
    resolved_mode = mode or settings.retrieval_mode
    target_collection = collection_name or _get_default_collection_name()

    if engine is None:
        engine = FastEmbedEngine()

    search_filter = build_metadata_filter(metadata, extra=extra_filter)
    needs_rerank = resolved_mode == RetrievalMode.HYBRID_RERANKED
    fetch_limit = (
        max(limit * DEFAULT_RERANK_CANDIDATE_MULTIPLIER, settings.rerank_candidate_limit)
        if needs_rerank
        else limit
    )

    start = time.perf_counter()
    embedded = engine.embed_query(query)

    if resolved_mode == RetrievalMode.DENSE_ONLY:
        response = client.query_points(
            collection_name=target_collection,
            query=embedded.dense,
            using=DENSE_VECTOR_NAME,
            query_filter=search_filter,
            limit=fetch_limit,
            with_payload=True,
        )
        raw_points = response.points
    else:
        prefetch_limit = max(fetch_limit * 4, 20)
        prefetches = [
            Prefetch(
                query=embedded.dense,
                using=DENSE_VECTOR_NAME,
                filter=search_filter,
                limit=prefetch_limit,
            ),
            Prefetch(
                query=SparseVector(
                    indices=embedded.sparse_indices,
                    values=embedded.sparse_values,
                ),
                using=SPARSE_VECTOR_NAME,
                filter=search_filter,
                limit=prefetch_limit,
            ),
        ]
        response = client.query_points(
            collection_name=target_collection,
            prefetch=prefetches,
            query=FusionQuery(fusion=Fusion.RRF),
            query_filter=search_filter,
            limit=fetch_limit,
            with_payload=True,
        )
        raw_points = response.points

    hits = _tie_break_sort([_validate_hit(point) for point in raw_points])

    if needs_rerank and hits:
        from app.retrieval.rerank import get_default_reranker

        reranker = get_default_reranker()
        hits = reranker.rerank(query, hits, top_n=limit)
    else:
        hits = hits[:limit]

    latency_ms = (time.perf_counter() - start) * 1000

    logger.debug(
        "hybrid_search.completed",
        mode=resolved_mode.value,
        collection=target_collection,
        hit_count=len(hits),
        latency_ms=round(latency_ms, 2),
    )

    return SearchResult(hits=hits, latency_ms=latency_ms, mode=resolved_mode)
