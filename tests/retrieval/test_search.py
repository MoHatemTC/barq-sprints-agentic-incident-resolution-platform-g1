"""Tests for hybrid search, metadata filtering, and mandatory safety invariants."""

from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.models import ScoredPoint
from qdrant_client.models import (
    FieldCondition,
    Filter,
    MatchValue,
)

from app.clients.qdrant import ensure_collection
from app.core.config import RetrievalMode
from app.models.knowledge import Article, SecurityLevel, WorkflowState
from app.retrieval.embedding import EmbeddedText, FastEmbedEngine
from app.retrieval.filters import MetadataFilterBuilder, build_metadata_filter
from app.retrieval.hybrid_search import (
    RetrievalHit,
    hybrid_search,
    timed_hybrid_search,
)
from app.retrieval.ingest import ingest_articles
from app.retrieval.sources import LocalJSONSource

CORPUS_PATH = Path("data/corpus/barq_articles.json")


@pytest.fixture
def memory_qdrant() -> QdrantClient:
    return QdrantClient(":memory:")


def _dummy_mock_engine() -> MagicMock:
    engine = MagicMock()
    engine.dense_vector_size = 384
    engine.embed_query.return_value = EmbeddedText(
        dense=[0.1] * 384,
        sparse_indices=[1, 2],
        sparse_values=[0.5, 0.8],
    )
    return engine


# ---------------------------------------------------------------------------
# Unit tests: Filter construction and merging
# ---------------------------------------------------------------------------


def test_default_filter_has_published_and_security() -> None:
    """With defaults, both mandatory conditions are present and nothing else.

    S3.5: the default workflow-state set gains human_resolved so
    knowledge-captured articles are retrievable the moment they land.
    """
    filt = build_metadata_filter()
    assert filt.must is not None
    assert len(filt.must) == 2

    wf_cond, sec_cond = filt.must
    assert isinstance(wf_cond, FieldCondition)
    assert wf_cond.key == "workflow_state"
    assert getattr(wf_cond.match, "any", None) == ["published", "human_resolved"]

    # #45: restricted content is excluded unless the caller opts in.
    assert isinstance(sec_cond, FieldCondition)
    assert sec_cond.key == "security_level"
    assert getattr(sec_cond.match, "any", None) == ["public", "internal"]


def test_filter_merged_with_extra_filter() -> None:
    """Extra filters are appended inside the parent must list."""
    extra = Filter(
        must=[FieldCondition(key="category", match=MatchValue(value="database"))],
        should=[FieldCondition(key="service", match=MatchValue(value="postgresql"))],
    )
    merged = build_metadata_filter(extra=extra)
    assert merged.must is not None
    assert len(merged.must) == 3

    # First clause is the non-negotiable published condition
    wf_cond = merged.must[0]
    assert isinstance(wf_cond, FieldCondition)
    assert wf_cond.key == "workflow_state"

    # Second is the equally non-negotiable audience condition (#45)
    sec_cond = merged.must[1]
    assert isinstance(sec_cond, FieldCondition)
    assert sec_cond.key == "security_level"

    # Third clause is the caller's extra filter intact
    assert merged.must[2] == extra


def test_metadata_builder_adds_category_filter() -> None:
    """MetadataFilterBuilder fields add to the must list."""
    metadata = MetadataFilterBuilder(category="network")
    filt = build_metadata_filter(metadata)
    # 2 mandatory (workflow_state, security_level) + 1 category
    assert filt.must is not None
    assert len(filt.must) == 3
    category_cond = filt.must[2]
    assert isinstance(category_cond, FieldCondition)
    assert category_cond.key == "category"
    assert getattr(category_cond.match, "value", None) == "network"


def test_metadata_builder_multi_value_service() -> None:
    """List values produce a MatchAny condition."""
    metadata = MetadataFilterBuilder(service=["corporate-vpn", "sap-erp"])
    filt = build_metadata_filter(metadata)
    service_cond = filt.must[2]
    assert service_cond.key == "service"
    assert getattr(service_cond.match, "any", None) == ["corporate-vpn", "sap-erp"]


def test_metadata_builder_restricted_security_includes_all_tiers() -> None:
    """Requesting RESTRICTED includes public + internal + restricted."""
    metadata = MetadataFilterBuilder(max_security_level=SecurityLevel.RESTRICTED)
    filt = build_metadata_filter(metadata)
    sec_cond = filt.must[1]
    assert getattr(sec_cond.match, "any", None) == ["public", "internal", "restricted"]


def test_metadata_builder_public_security_only_public() -> None:
    """Requesting PUBLIC limits to just public."""
    metadata = MetadataFilterBuilder(max_security_level=SecurityLevel.PUBLIC)
    filt = build_metadata_filter(metadata)
    sec_cond = filt.must[1]
    assert getattr(sec_cond.match, "any", None) == ["public"]


def test_empty_filter_value_list_raises() -> None:
    """An empty list for a filter field must raise, not silently match nothing."""
    metadata = MetadataFilterBuilder(service=[])
    with pytest.raises(ValueError, match="must not be empty"):
        build_metadata_filter(metadata)


def test_extra_filter_cannot_override_published(memory_qdrant: QdrantClient) -> None:
    """If a caller tries to demand workflow_state='retired', 0 hits are safely returned."""
    col = "override_test"
    ensure_collection(memory_qdrant, col)

    # Ingest a retired article
    retired_art = Article(
        article_number="KB0099",
        version="1.0",
        title="Retired Article",
        short_description="Old article",
        category="database",
        service="db",
        workflow_state=WorkflowState.RETIRED,
        security_level=SecurityLevel.INTERNAL,
        body="Old body procedure",
    )
    engine = _dummy_mock_engine()
    engine.embed_documents.return_value = [
        EmbeddedText(dense=[0.1] * 384, sparse_indices=[1], sparse_values=[1.0])
    ]
    ingest_articles([retired_art], memory_qdrant, col, engine)

    caller_filter = Filter(
        must=[FieldCondition(key="workflow_state", match=MatchValue(value="retired"))]
    )
    hits = hybrid_search(
        memory_qdrant,
        "database query",
        collection_name=col,
        extra_filter=caller_filter,
        engine=engine,
    )
    assert hits == []


# ---------------------------------------------------------------------------
# Unit tests: Spy verification of dual-filter placement
# ---------------------------------------------------------------------------


def test_filter_applied_to_both_prefetches() -> None:
    """Dual-filter requirement: dense and sparse prefetches both carry the filter."""
    spy_client = MagicMock(spec=QdrantClient)
    spy_client.query_points.return_value = MagicMock(points=[])

    engine = _dummy_mock_engine()
    hybrid_search(
        spy_client,
        "test query",
        collection_name="col",
        engine=engine,
    )

    assert spy_client.query_points.called
    kwargs = spy_client.query_points.call_args.kwargs
    prefetches = kwargs["prefetch"]
    assert len(prefetches) == 2

    dense_prefetch = prefetches[0]
    sparse_prefetch = prefetches[1]

    # Verify both prefetches have the filter
    assert dense_prefetch.filter is not None
    assert sparse_prefetch.filter is not None

    for pf in (dense_prefetch, sparse_prefetch):
        wf_cond = pf.filter.must[0]
        assert wf_cond.key == "workflow_state"


def test_filter_applied_to_top_level_query() -> None:
    """Top-level query_filter carries the filter for defense-in-depth."""
    spy_client = MagicMock(spec=QdrantClient)
    spy_client.query_points.return_value = MagicMock(points=[])

    engine = _dummy_mock_engine()
    hybrid_search(
        spy_client,
        "test query",
        collection_name="col",
        engine=engine,
    )

    kwargs = spy_client.query_points.call_args.kwargs
    top_filter = kwargs.get("query_filter")
    assert top_filter is not None
    assert top_filter.must[0].key == "workflow_state"


# ---------------------------------------------------------------------------
# Unit tests: Dense-only mode uses query_points without prefetch
# ---------------------------------------------------------------------------


def test_dense_only_mode_no_prefetch() -> None:
    """In DENSE_ONLY mode, query_points is called with query= (not prefetch=)."""
    spy_client = MagicMock(spec=QdrantClient)
    spy_client.query_points.return_value = MagicMock(points=[])

    engine = _dummy_mock_engine()
    hybrid_search(
        spy_client,
        "test query",
        collection_name="col",
        engine=engine,
        mode=RetrievalMode.DENSE_ONLY,
    )

    kwargs = spy_client.query_points.call_args.kwargs
    assert "prefetch" not in kwargs or kwargs.get("prefetch") is None
    assert kwargs.get("query") is not None


def test_hybrid_reranked_mode_fetches_more_and_reranks(monkeypatch: pytest.MonkeyPatch) -> None:
    """HYBRID_RERANKED expands the fetch limit and calls the cross-encoder."""

    # Mock the Qdrant client to return a dummy point
    spy_client = MagicMock(spec=QdrantClient)
    dummy_point = ScoredPoint(
        id="dummy-uuid",
        version=1,
        score=0.5,
        payload={
            "article_number": "KB0001",
            "version": "1.0",
            "title": "Title",
            "category": "software",
            "service": "app",
            "workflow_state": "published",
            "security_level": "public",
            "section": "General",
            "chunk_index": 0,
            "total_chunks": 1,
            "chunk_text": "Text",
        },
        vector=None,
    )
    spy_client.query_points.return_value = MagicMock(points=[dummy_point])

    # Mock the reranker to observe the call
    spy_reranker = MagicMock()
    spy_reranker.rerank.return_value = [
        RetrievalHit(
            score=0.99,
            article_id="KB0001-v1.0",
            article_number="KB0001",
            version="1.0",
            title="Title",
            section="General",
            chunk_index=0,
            chunk_text="Text",
            workflow_state="published",
            security_level="public",
            category="software",
            service="app",
        )
    ]
    monkeypatch.setattr("app.retrieval.rerank.get_default_reranker", lambda: spy_reranker)

    engine = _dummy_mock_engine()
    hits = hybrid_search(
        spy_client,
        "test query",
        collection_name="col",
        limit=2,
        engine=engine,
        mode=RetrievalMode.HYBRID_RERANKED,
    )

    # Verify Qdrant was called with an expanded fetch_limit for reranking
    kwargs = spy_client.query_points.call_args.kwargs
    assert kwargs["limit"] >= 8  # DEFAULT_RERANK_CANDIDATE_MULTIPLIER=4 * 2

    # Verify the reranker was called
    assert spy_reranker.rerank.called
    assert hits[0].score == 0.99


# ---------------------------------------------------------------------------
# Unit tests: Empty results and error handling
# ---------------------------------------------------------------------------


def test_blank_query_raises_loudly() -> None:
    """An empty/whitespace query is invalid input and must fail loud, not return []."""
    spy_client = MagicMock(spec=QdrantClient)
    with pytest.raises(ValueError, match="query must be a non-empty string"):
        hybrid_search(spy_client, "")
    with pytest.raises(ValueError, match="query must be a non-empty string"):
        hybrid_search(spy_client, "   ")
    assert not spy_client.query_points.called


def test_empty_results_return_empty_list() -> None:
    """Zero matching points returns an empty list gracefully."""
    spy_client = MagicMock(spec=QdrantClient)
    spy_client.query_points.return_value = MagicMock(points=[])
    hits = hybrid_search(
        spy_client,
        "some query",
        collection_name="col",
        engine=_dummy_mock_engine(),
    )
    assert hits == []


def test_malformed_payload_raises() -> None:
    """Missing required payload fields raises ValueError citing the offending point ID."""
    spy_client = MagicMock(spec=QdrantClient)
    bad_point = ScoredPoint(
        id="bad-point-uuid",
        version=1,
        score=0.42,
        payload={"title": "Incomplete Payload"},  # missing article_number, version, etc.
        vector=None,
    )
    spy_client.query_points.return_value = MagicMock(points=[bad_point])

    with pytest.raises(ValueError, match="bad-point-uuid.*malformed payload"):
        hybrid_search(
            spy_client,
            "test query",
            collection_name="col",
            engine=_dummy_mock_engine(),
        )


def test_wrong_typed_payload_value_raises() -> None:
    """A present-but-invalid value (title=None) fails loud instead of becoming the text 'None'."""
    spy_client = MagicMock(spec=QdrantClient)
    bad_point = ScoredPoint(
        id="null-title-uuid",
        version=1,
        score=0.42,
        payload={
            "article_number": "KB0010",
            "version": "2.0",
            "title": None,  # wrong type: str field receiving None
            "category": "software",
            "service": "order-service",
            "workflow_state": "published",
            "security_level": "internal",
            "section": "Resolution",
            "chunk_index": 0,
            "total_chunks": 1,
            "chunk_text": "Drain the connection pool.",
        },
        vector=None,
    )
    spy_client.query_points.return_value = MagicMock(points=[bad_point])

    with pytest.raises(ValueError, match="null-title-uuid.*malformed payload"):
        hybrid_search(
            spy_client,
            "test query",
            collection_name="col",
            engine=_dummy_mock_engine(),
        )


def test_timed_search_returns_search_result() -> None:
    """timed_hybrid_search returns a SearchResult with latency_ms and mode."""
    spy_client = MagicMock(spec=QdrantClient)
    spy_client.query_points.return_value = MagicMock(points=[])

    result = timed_hybrid_search(
        spy_client,
        "test query",
        collection_name="col",
        engine=_dummy_mock_engine(),
    )

    assert result.hits == []
    assert result.latency_ms >= 0
    assert isinstance(result.mode, RetrievalMode)


def test_settings_errors_stop_retrieval_instead_of_switching_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken config must fail, not silently search a different collection.

    Previously any exception fell back to ``incident_knowledge_base``, so an unrelated
    bad setting redirected retrieval away from the configured collection.
    """
    import app.core.config as config_module

    def _boom() -> object:
        raise RuntimeError("QDRANT_HTTP_PORT is not a valid integer")

    monkeypatch.setattr(config_module, "get_retrieval_settings", _boom)

    with pytest.raises(RuntimeError, match="QDRANT_HTTP_PORT"):
        hybrid_search(MagicMock(), "any query")


# ---------------------------------------------------------------------------
# Acceptance tests: Real corpus + Real FastEmbed + :memory: Qdrant
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def seeded_acceptance_qdrant() -> tuple[QdrantClient, FastEmbedEngine, str]:
    """Ingest the real 11-article corpus into in-memory Qdrant once for acceptance tests."""
    client = QdrantClient(":memory:")
    collection_name = "test_acceptance_kb"
    engine = FastEmbedEngine()

    ensure_collection(
        client,
        collection_name,
        dense_vector_size=engine.dense_vector_size,
    )

    articles = LocalJSONSource(CORPUS_PATH).load_articles()
    ingest_articles(articles, client, collection_name, engine)
    return client, engine, collection_name


@pytest.mark.skipif(
    not CORPUS_PATH.exists(),
    reason="real corpus barq_articles.json not found",
)
def test_p3_kb0010_v1_never_returned(
    seeded_acceptance_qdrant: tuple[QdrantClient, FastEmbedEngine, str],
) -> None:
    """P3 Acceptance Test: KB0010-v1.0 must NEVER appear in results for pool exhaustion."""
    client, engine, collection_name = seeded_acceptance_qdrant
    query = "the order service is returning errors and the pool is exhausted"

    metadata = MetadataFilterBuilder(max_security_level=SecurityLevel.RESTRICTED)
    hits = hybrid_search(
        client,
        query,
        collection_name=collection_name,
        limit=5,
        metadata=metadata,
        engine=engine,
    )

    assert len(hits) > 0, "Expected at least one matching hit for pool exhaustion query"
    article_ids = [h.article_id for h in hits]
    assert "KB0010-v1.0" not in article_ids, (
        f"SAFETY VIOLATION (P3): Retired KB0010-v1.0 returned in search results: {article_ids}"
    )


@pytest.mark.skipif(
    not CORPUS_PATH.exists(),
    reason="real corpus barq_articles.json not found",
)
def test_all_hits_are_published(
    seeded_acceptance_qdrant: tuple[QdrantClient, FastEmbedEngine, str],
) -> None:
    """P3 Acceptance Test: Every returned hit must have workflow_state == 'published'."""
    client, engine, collection_name = seeded_acceptance_qdrant
    query = "the order service is returning errors and the pool is exhausted"

    metadata = MetadataFilterBuilder(max_security_level=SecurityLevel.RESTRICTED)
    hits = hybrid_search(
        client,
        query,
        collection_name=collection_name,
        limit=5,
        metadata=metadata,
        engine=engine,
    )

    assert len(hits) > 0
    for hit in hits:
        assert hit.workflow_state == "published", (
            f"Non-published hit returned: {hit.article_id} with state {hit.workflow_state}"
        )


@pytest.mark.skipif(
    not CORPUS_PATH.exists(),
    reason="real corpus barq_articles.json not found",
)
def test_p3_kb0010_v2_present(
    seeded_acceptance_qdrant: tuple[QdrantClient, FastEmbedEngine, str],
) -> None:
    """P3 Acceptance Test: Current KB0010-v2.0 MUST be returned for pool exhaustion.

    KB0010 is a ``restricted`` article, so this asks for that tier explicitly. Before
    #45 no caller had to: every restricted article was returned to everyone.
    """
    client, engine, collection_name = seeded_acceptance_qdrant
    query = "the order service is returning errors and the pool is exhausted"

    metadata = MetadataFilterBuilder(max_security_level=SecurityLevel.RESTRICTED)
    hits = hybrid_search(
        client,
        query,
        collection_name=collection_name,
        limit=5,
        metadata=metadata,
        engine=engine,
    )

    article_ids = [h.article_id for h in hits]
    assert "KB0010-v2.0" in article_ids, (
        f"Expected KB0010-v2.0 in top results for pool exhaustion query, got: {article_ids}"
    )


# ---------------------------------------------------------------------------
# Corpus invariant test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not CORPUS_PATH.exists(),
    reason="real corpus barq_articles.json not found",
)
def test_corpus_max_one_published_per_article_number() -> None:
    """Corpus invariant: At most one published version exists per article_number."""
    articles = LocalJSONSource(CORPUS_PATH).load_articles()
    published_articles = [a for a in articles if a.workflow_state == WorkflowState.PUBLISHED]
    counts = Counter(a.article_number for a in published_articles)
    for art_num, count in counts.items():
        assert count <= 1, f"Corpus invariant violated: {art_num} has {count} published versions!"


# ---------------------------------------------------------------------------
# #45 — restricted knowledge must not reach callers that did not ask for it
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not CORPUS_PATH.exists(),
    reason="real corpus barq_articles.json not found",
)
def test_restricted_articles_are_excluded_by_default(
    seeded_acceptance_qdrant: tuple[QdrantClient, FastEmbedEngine, str],
) -> None:
    """The default audience never sees restricted content.

    5 of the 11 corpus articles are ``restricted``. Before #45 every one of them was
    returned to every caller, and ``RetrievalHit`` did not even carry the level, so a
    caller could not have filtered afterwards.
    """
    client, engine, collection_name = seeded_acceptance_qdrant
    query = "the order service is returning errors and the pool is exhausted"

    hits = hybrid_search(
        client,
        query,
        collection_name=collection_name,
        limit=10,
        engine=engine,
    )

    assert hits, "expected the default audience to still get results"
    assert all(h.security_level != "restricted" for h in hits), (
        f"restricted content leaked: {[(h.article_id, h.security_level) for h in hits]}"
    )


@pytest.mark.skipif(
    not CORPUS_PATH.exists(),
    reason="real corpus barq_articles.json not found",
)
def test_restricted_articles_are_returned_when_explicitly_requested(
    seeded_acceptance_qdrant: tuple[QdrantClient, FastEmbedEngine, str],
) -> None:
    """Opting in is what makes the restricted tier reachable — and it is auditable."""
    client, engine, collection_name = seeded_acceptance_qdrant
    query = "the order service is returning errors and the pool is exhausted"

    metadata = MetadataFilterBuilder(max_security_level=SecurityLevel.RESTRICTED)
    hits = hybrid_search(
        client,
        query,
        collection_name=collection_name,
        limit=10,
        metadata=metadata,
        engine=engine,
    )

    assert any(h.security_level == "restricted" for h in hits), (
        "explicitly requesting the restricted tier returned none of it"
    )
