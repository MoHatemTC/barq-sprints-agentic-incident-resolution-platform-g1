"""Hybrid vector search entry point with mandatory workflow_state filtering.

Single retrieval entry point for the BARQ incident resolution platform.
Implements hybrid retrieval (dense Cosine + sparse BM25) fused via Reciprocal
Rank Fusion (RRF), enforcing the safety-critical P3 invariant:
**retired and draft knowledge articles are strictly excluded**.
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, ConfigDict
from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchValue,
    Prefetch,
    SparseVector,
)

from app.clients.qdrant import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME
from app.retrieval.embedding import EmbeddingEngine, FastEmbedEngine

logger = structlog.get_logger(__name__)

DEFAULT_COLLECTION_NAME = "incident_knowledge_base"


def _get_default_collection_name() -> str:
    try:
        from app.core.config import get_retrieval_settings

        return get_retrieval_settings().qdrant_collection_name
    except Exception:
        return DEFAULT_COLLECTION_NAME


class RetrievalHit(BaseModel):
    """A scored knowledge chunk returned from hybrid vector retrieval.

    Note on score semantics:
    In hybrid search with Reciprocal Rank Fusion (RRF), `score` is a rank sum:
        score = sum(1 / (k + rank_i))
    It is NOT a cosine similarity or distance metric (values are small positive
    floats, typically between 0.01 and 0.5 with default k=2). Never compare this
    score directly against cosine similarity thresholds.
    """

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
    category: str  # Knowledge category, e.g. "database", "network"
    service: str | None = None  # Affected service name, e.g. "postgres", "redis"


def _build_filter(extra: Filter | None = None) -> Filter:
    """Build the retrieval filter, unconditionally requiring published workflow_state.

    `workflow_state == 'published'` is non-negotiable and enforced at the entry point.
    Callers may only supply additional narrowing criteria (via `extra`).
    If `extra` is provided, it is wrapped within a parent `must` list:
        Filter(must=[published_condition, extra])
    This preserves any caller-specified `should` or `must_not` clauses, and guarantees
    that a caller demanding an incompatible state (e.g. `retired`) safely returns 0 hits.
    """
    published_condition = FieldCondition(
        key="workflow_state",
        match=MatchValue(value="published"),
    )
    if extra is None:
        return Filter(must=[published_condition])
    return Filter(must=[published_condition, extra])


def retrieve_knowledge(
    client: QdrantClient,
    query: str,
    *,
    collection_name: str | None = None,
    limit: int = 5,
    extra_filter: Filter | None = None,
    engine: EmbeddingEngine | None = None,
) -> list[RetrievalHit]:
    """Retrieve top knowledge chunks using hybrid dense+sparse search and RRF fusion.

    Always enforces `workflow_state == 'published'`.

    Args:
        client: Active Qdrant client connection.
        query: Query text (e.g., incident description or error message).
        collection_name: Target collection. If None, resolves from RetrievalSettings.
            Production callers should leave this as None to use the configured collection.
        limit: Maximum number of final fused hits to return (top_k).
        extra_filter: Optional Qdrant Filter to further narrow results (e.g. by service,
            category, or security level). Cannot bypass the mandatory published filter.
        engine: Embedding engine used to vectorize the query into dense and sparse vectors.
            If None, instantiates a new FastEmbedEngine. NOTE: Initializing FastEmbedEngine
            loads model weights from disk and is expensive; production callers should
            create and cache a shared engine instance.

    Returns:
        A list of `RetrievalHit` objects ordered by descending RRF fusion rank score.

    Raises:
        ValueError: If a retrieved point has missing or malformed payload fields.
    """
    if not query or not query.strip():
        return []

    target_collection = collection_name or _get_default_collection_name()

    if engine is None:
        engine = FastEmbedEngine()

    search_filter = _build_filter(extra_filter)
    prefetch_limit = max(limit * 4, 20)

    embedded = engine.embed_query(query)

    # Dual-filter placement:
    # 1. Prefetch filter: Mandatory on :memory: backend (mock engine ignores top-level
    #    filter on FusionQuery) and critical on server to prevent candidate starvation.
    # 2. Top-level query_filter: Defense-in-depth on server Qdrant.
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
        limit=limit,
        with_payload=True,
    )

    hits: list[RetrievalHit] = []
    for point in response.points:
        payload = point.payload
        if payload is None:
            raise ValueError(f"Point {point.id} returned without payload")

        try:
            hit = RetrievalHit(
                score=float(point.score),
                article_id=str(payload["article_id"]),
                article_number=str(payload["article_number"]),
                version=str(payload["version"]),
                title=str(payload["title"]),
                section=str(payload["section"]),
                chunk_index=int(payload["chunk_index"]),
                chunk_text=str(payload["chunk_text"]),
                workflow_state=str(payload["workflow_state"]),
                category=str(payload["category"]),
                service=payload.get("service"),
            )
            hits.append(hit)
        except (KeyError, TypeError, ValueError) as err:
            raise ValueError(
                f"Point {point.id} has malformed payload missing required field: {err}"
            ) from err

    return hits
