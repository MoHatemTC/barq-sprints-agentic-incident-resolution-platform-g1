"""Hybrid vector search entry point with mandatory workflow_state filtering.

Single retrieval entry point for the BARQ incident resolution platform.
Implements hybrid retrieval (dense Cosine + sparse BM25) fused via Reciprocal
Rank Fusion (RRF), enforcing the safety-critical P3 invariant:
**retired and draft knowledge articles are strictly excluded**.
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Condition,
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchValue,
    Prefetch,
    SparseVector,
)

from app.clients.qdrant import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME
from app.models.knowledge import KnowledgePayload, SecurityLevel
from app.retrieval.embedding import EmbeddingEngine, FastEmbedEngine

logger = structlog.get_logger(__name__)

DEFAULT_COLLECTION_NAME = "incident_knowledge_base"

#: Increasing sensitivity. An audience cleared for a level may read every level at or
#: below it.
SECURITY_LEVEL_ORDER: tuple[SecurityLevel, ...] = (
    SecurityLevel.PUBLIC,
    SecurityLevel.INTERNAL,
    SecurityLevel.RESTRICTED,
)

#: Safe default: callers get non-restricted content unless they ask for more. 5 of the
#: 11 corpus records are ``restricted``, and before #45 every one of them was returned
#: to every caller.
DEFAULT_MAX_SECURITY_LEVEL = SecurityLevel.INTERNAL


def _get_default_collection_name() -> str:
    """Resolve the configured collection name.

    Settings errors propagate. This used to fall back to DEFAULT_COLLECTION_NAME on any
    exception, so an unrelated bad setting (an invalid QDRANT_HTTP_PORT, say) silently
    redirected retrieval from the configured collection to ``incident_knowledge_base``.
    A broken config must stop the run, not quietly search somewhere else. See #45.
    """
    from app.core.config import get_retrieval_settings

    return get_retrieval_settings().qdrant_collection_name


def _allowed_security_levels(max_level: SecurityLevel) -> list[str]:
    """Every level at or below ``max_level``, as payload strings."""
    cutoff = SECURITY_LEVEL_ORDER.index(max_level)
    return [level.value for level in SECURITY_LEVEL_ORDER[: cutoff + 1]]


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
    security_level: str  # Audience tier: "public", "internal" or "restricted"
    category: str  # Knowledge category, e.g. "database", "network"
    service: str | None = None  # Affected service name, e.g. "postgres", "redis"


def _build_filter(
    extra: Filter | None = None,
    max_security_level: SecurityLevel = DEFAULT_MAX_SECURITY_LEVEL,
) -> Filter:
    """Build the retrieval filter, requiring published state and an allowed audience.

    Two conditions are non-negotiable and enforced at the entry point:

    - `workflow_state == 'published'` — retired and draft articles never returned.
    - `security_level` within `max_security_level` — restricted content is excluded
      unless the caller explicitly asks for it (#45).

    Callers may only supply additional narrowing criteria (via `extra`). If `extra` is
    provided, it is wrapped within a parent `must` list, which preserves any
    caller-specified `should` or `must_not` clauses and guarantees that a caller
    demanding an incompatible value (e.g. `retired`) safely returns 0 hits.
    """
    # Typed as the union Filter accepts: `must` is invariant, so a bare
    # list[FieldCondition] is rejected once `extra` (a Filter) joins the list.
    mandatory: list[Condition] = [
        FieldCondition(
            key="workflow_state",
            match=MatchValue(value="published"),
        ),
        FieldCondition(
            key="security_level",
            match=MatchAny(any=_allowed_security_levels(max_security_level)),
        ),
    ]
    if extra is None:
        return Filter(must=mandatory)
    return Filter(must=[*mandatory, extra])


def retrieve_knowledge(
    client: QdrantClient,
    query: str,
    *,
    collection_name: str | None = None,
    limit: int = 5,
    extra_filter: Filter | None = None,
    max_security_level: SecurityLevel = DEFAULT_MAX_SECURITY_LEVEL,
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
        extra_filter: Optional Qdrant Filter to further narrow results (e.g. by service
            or category). Cannot bypass the mandatory published or security filters.
        max_security_level: Highest audience tier the caller is cleared for. Defaults to
            `SecurityLevel.INTERNAL`, so `restricted` articles are excluded unless a
            caller explicitly opts in. Pass `SecurityLevel.RESTRICTED` only for callers
            actually cleared for it.
        engine: Embedding engine used to vectorize the query into dense and sparse vectors.
            If None, instantiates a new FastEmbedEngine. NOTE: Initializing FastEmbedEngine
            loads model weights from disk and is expensive; production callers should
            create and cache a shared engine instance.

    Returns:
        A list of `RetrievalHit` objects ordered by descending RRF fusion rank score.

    Raises:
        ValueError: If the query is empty or whitespace-only, or if a retrieved
            point has missing or malformed payload fields.
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")

    target_collection = collection_name or _get_default_collection_name()

    if engine is None:
        engine = FastEmbedEngine()

    search_filter = _build_filter(extra_filter, max_security_level)
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
            # Validate against the ingestion contract first: missing keys AND
            # wrong-typed values (e.g. title=None) must fail loud, not coerce
            # into plausible-looking hits.
            validated = KnowledgePayload.model_validate(payload)
            hit = RetrievalHit(
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
            hits.append(hit)
        except (ValidationError, TypeError, ValueError) as err:
            raise ValueError(
                f"Point {point.id} has malformed payload violating the ingestion contract: {err}"
            ) from err

    return hits
