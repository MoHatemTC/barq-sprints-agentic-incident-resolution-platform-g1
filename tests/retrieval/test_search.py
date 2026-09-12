"""Tests for single retrieval entry point and mandatory workflow_state filtering (P3)."""

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
from app.models.knowledge import Article, SecurityLevel, WorkflowState
from app.retrieval.embedding import EmbeddedText, FastEmbedEngine
from app.retrieval.ingest import ingest_articles
from app.retrieval.search import (
    _build_filter,
    retrieve_knowledge,
)
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


def test_published_filter_present_without_extra_filter() -> None:
    """When extra_filter=None, _build_filter produces must=[workflow_state == 'published']."""
    filt = _build_filter(None)
    assert filt.must is not None
    assert len(filt.must) == 1
    cond = filt.must[0]
    assert isinstance(cond, FieldCondition)
    assert cond.key == "workflow_state"
    assert getattr(cond.match, "value", None) == "published"


def test_published_filter_merged_with_extra_filter() -> None:
    """Extra filters are wrapped inside parent must, preserving should/must_not."""
    extra = Filter(
        must=[FieldCondition(key="category", match=MatchValue(value="database"))],
        should=[FieldCondition(key="service", match=MatchValue(value="postgresql"))],
    )
    merged = _build_filter(extra)
    assert merged.must is not None
    assert len(merged.must) == 2

    # First clause is the non-negotiable published condition
    published_cond = merged.must[0]
    assert isinstance(published_cond, FieldCondition)
    assert published_cond.key == "workflow_state"
    assert getattr(published_cond.match, "value", None) == "published"

    # Second clause is the caller's extra filter intact
    assert merged.must[1] == extra


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
    hits = retrieve_knowledge(
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
    """Dual-filter requirement: dense and sparse prefetches both carry the published filter."""
    spy_client = MagicMock(spec=QdrantClient)
    spy_client.query_points.return_value = MagicMock(points=[])

    engine = _dummy_mock_engine()
    retrieve_knowledge(
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
        cond = pf.filter.must[0]
        assert cond.key == "workflow_state"
        assert getattr(cond.match, "value", None) == "published"


def test_filter_applied_to_top_level_query() -> None:
    """Top-level query_filter carries the published filter for defense-in-depth."""
    spy_client = MagicMock(spec=QdrantClient)
    spy_client.query_points.return_value = MagicMock(points=[])

    engine = _dummy_mock_engine()
    retrieve_knowledge(
        spy_client,
        "test query",
        collection_name="col",
        engine=engine,
    )

    kwargs = spy_client.query_points.call_args.kwargs
    top_filter = kwargs.get("query_filter")
    assert top_filter is not None
    assert top_filter.must[0].key == "workflow_state"
    assert getattr(top_filter.must[0].match, "value", None) == "published"


# ---------------------------------------------------------------------------
# Unit tests: Empty results and error handling
# ---------------------------------------------------------------------------


def test_empty_results_return_empty_list() -> None:
    """Empty query string or zero matching points returns empty list gracefully."""
    spy_client = MagicMock(spec=QdrantClient)
    assert retrieve_knowledge(spy_client, "") == []
    assert retrieve_knowledge(spy_client, "   ") == []

    spy_client.query_points.return_value = MagicMock(points=[])
    hits = retrieve_knowledge(
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
        retrieve_knowledge(
            spy_client,
            "test query",
            collection_name="col",
            engine=_dummy_mock_engine(),
        )


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

    hits = retrieve_knowledge(
        client,
        query,
        collection_name=collection_name,
        limit=5,
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

    hits = retrieve_knowledge(
        client,
        query,
        collection_name=collection_name,
        limit=5,
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
    """P3 Acceptance Test: Current KB0010-v2.0 MUST be returned for pool exhaustion."""
    client, engine, collection_name = seeded_acceptance_qdrant
    query = "the order service is returning errors and the pool is exhausted"

    hits = retrieve_knowledge(
        client,
        query,
        collection_name=collection_name,
        limit=5,
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
