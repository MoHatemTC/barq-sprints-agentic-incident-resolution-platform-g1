"""Tests for Qdrant knowledge base ingestion pipeline."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from qdrant_client import QdrantClient

from app.clients.qdrant import ensure_collection
from app.models.knowledge import Article, SecurityLevel, WorkflowState
from app.retrieval.embedding import EmbeddedText
from app.retrieval.ingest import (
    KB_NAMESPACE,
    build_point_id,
    ingest_articles,
)
from retrieval.ingest import ingest_articles as shim_ingest


@pytest.fixture
def memory_qdrant() -> QdrantClient:
    return QdrantClient(":memory:")


@pytest.fixture
def sample_articles() -> list[Article]:
    return [
        Article(
            article_number="KB0001",
            version="2.0",
            title="VPN authentication fails after a password change",
            short_description="Clear cached VPN credentials after password reset.",
            category="network",
            service="corporate-vpn",
            workflow_state=WorkflowState.PUBLISHED,
            security_level=SecurityLevel.INTERNAL,
            body=(
                "# VPN authentication fails\n\n"
                "## Symptom\nAuthentication fails after password change.\n\n"
                "## Cause\nCached credentials in credential store.\n\n"
                "## Resolution\nClear cached credential and reconnect.\n\n"
                "## Escalation\nEscalate to Network Operations.\n"
            ),
        ),
        Article(
            article_number="KB0007",
            version="2.0",
            title="Laptop performance degrades after a system update",
            short_description="Driver mismatch or background indexing following update.",
            category="hardware",
            service="endpoint",
            workflow_state=WorkflowState.PUBLISHED,
            security_level=SecurityLevel.RESTRICTED,
            body=(
                "# Laptop performance degrades\n\n"
                "## Symptom\nLaptop is noticeably slow after update.\n\n"
                "## Cause\nBackground indexing or graphics driver mismatch.\n\n"
                "## Resolution\nWait 24h or reinstall vendor driver.\n\n"
                "## Escalation\nEscalate to Endpoint Engineering.\n"
            ),
        ),
    ]


def test_build_point_id_is_deterministic() -> None:
    id1 = build_point_id("KB0001-v2.0", 0)
    id2 = build_point_id("KB0001-v2.0", 0)
    id3 = build_point_id("KB0001-v2.0", 1)
    id4 = build_point_id("KB0002-v1.0", 0)

    assert id1 == id2, "Same input must produce identical UUID"
    assert id1 != id3, "Different chunk index must produce different UUID"
    assert id1 != id4, "Different article ID must produce different UUID"


def test_build_point_id_uses_dedicated_namespace() -> None:
    """Point IDs must derive from the KB namespace, not raw NAMESPACE_DNS."""
    import uuid

    point_id = build_point_id("KB0001-v2.0", 0)
    assert point_id == str(uuid.uuid5(KB_NAMESPACE, "KB0001-v2.0::chunk::0"))
    raw_dns = str(uuid.uuid5(uuid.NAMESPACE_DNS, "KB0001-v2.0::chunk::0"))
    assert point_id != raw_dns, "KB namespace must differ from the raw DNS namespace"


def test_setup_qdrant_collection(memory_qdrant: QdrantClient) -> None:
    col_name = "test_kb"
    ensure_collection(memory_qdrant, col_name)

    assert memory_qdrant.collection_exists(col_name)
    info = memory_qdrant.get_collection(col_name)
    assert "dense" in info.config.params.vectors  # type: ignore[operator]
    assert "sparse" in info.config.params.sparse_vectors  # type: ignore[operator]


def test_setup_qdrant_collection_recreate(memory_qdrant: QdrantClient) -> None:
    col_name = "recreate_kb"
    ensure_collection(memory_qdrant, col_name)
    assert memory_qdrant.collection_exists(col_name)

    # Recreate without error
    ensure_collection(memory_qdrant, col_name, force_recreate=True)
    assert memory_qdrant.collection_exists(col_name)


def test_ingest_articles_with_mock_embedding(
    memory_qdrant: QdrantClient, sample_articles: list[Article]
) -> None:
    col_name = "ingest_test"
    ensure_collection(memory_qdrant, col_name)

    mock_engine = MagicMock()

    # Mock embed_documents to return vectors sized to match total chunks
    def fake_embed(docs: list[str]) -> list[EmbeddedText]:
        return [
            EmbeddedText(
                dense=[0.1] * 384,
                sparse_indices=[1, 2],
                sparse_values=[0.5, 0.8],
            )
            for _ in docs
        ]

    mock_engine.embed_documents.side_effect = fake_embed

    count = ingest_articles(
        articles=sample_articles,
        client=memory_qdrant,
        collection_name=col_name,
        embedding_engine=mock_engine,
    )

    assert count > 0
    # Verify mock was called once with all chunks combined (IDF fitting rule)
    assert mock_engine.embed_documents.call_count == 1
    call_docs = mock_engine.embed_documents.call_args[0][0]
    assert len(call_docs) == count

    info = memory_qdrant.get_collection(col_name)
    assert info.points_count == count

    # Verify a retrieved point has all required payload fields
    points, _ = memory_qdrant.scroll(collection_name=col_name, limit=1, with_payload=True)
    assert len(points) == 1
    p = points[0].payload
    assert p is not None
    assert "article_number" in p
    assert "article_id" in p
    assert "category" in p
    assert "service" in p
    assert "workflow_state" in p
    assert "version" in p
    assert "security_level" in p
    assert "section" in p
    assert "chunk_text" in p


def test_ingest_idempotency(memory_qdrant: QdrantClient, sample_articles: list[Article]) -> None:
    col_name = "idempotent_test"
    ensure_collection(memory_qdrant, col_name)

    mock_engine = MagicMock()
    mock_engine.embed_documents.side_effect = lambda docs: [
        EmbeddedText(
            dense=[0.1] * 384,
            sparse_indices=[1],
            sparse_values=[1.0],
        )
        for _ in docs
    ]

    count1 = ingest_articles(sample_articles, memory_qdrant, col_name, mock_engine)
    points1, _ = memory_qdrant.scroll(collection_name=col_name, limit=256, with_payload=False)
    ids1 = {str(p.id) for p in points1}

    # Ingest same articles second time
    count2 = ingest_articles(sample_articles, memory_qdrant, col_name, mock_engine)
    points2, _ = memory_qdrant.scroll(collection_name=col_name, limit=256, with_payload=False)
    ids2 = {str(p.id) for p in points2}

    assert count1 == count2
    assert len(ids1) == len(ids2), "Re-ingesting must not duplicate points"
    assert ids1 == ids2, "Re-ingesting must produce identical point IDs (in-place upsert)"


def test_ingest_empty_articles_raises(memory_qdrant: QdrantClient) -> None:
    """Seeding nothing is a configuration error, not a quiet no-op."""
    mock_engine = MagicMock()
    with pytest.raises(ValueError, match="no articles"):
        ingest_articles([], memory_qdrant, "empty_test", mock_engine)
    assert mock_engine.embed_documents.call_count == 0


def test_ingest_real_barq_corpus(memory_qdrant: QdrantClient) -> None:
    corpus_file = Path("data/corpus/barq_articles.json")
    if not corpus_file.exists():
        pytest.skip("Real barq_articles.json not found")

    with open(corpus_file, encoding="utf-8") as f:
        data = json.load(f)

    articles = [Article.model_validate(item) for item in data]
    assert len(articles) == 11

    col_name = "barq_real_kb"
    ensure_collection(memory_qdrant, col_name)

    mock_engine = MagicMock()
    mock_engine.embed_documents.side_effect = lambda docs: [
        EmbeddedText(
            dense=[0.01] * 384,
            sparse_indices=[1, 2],
            sparse_values=[0.5, 0.8],
        )
        for _ in docs
    ]

    # 1. Ingest all 11 articles -> exactly 45 points
    total_upserted = ingest_articles(
        articles=articles,
        client=memory_qdrant,
        collection_name=col_name,
        embedding_engine=mock_engine,
        chunk_size=700,
        chunk_overlap=120,
    )
    assert total_upserted == 45
    info = memory_qdrant.get_collection(col_name)
    assert info.points_count == 45

    # 2. Re-ingest the same articles -> count stays exactly 45 (idempotency)
    reingested = ingest_articles(
        articles=articles,
        client=memory_qdrant,
        collection_name=col_name,
        embedding_engine=mock_engine,
        chunk_size=700,
        chunk_overlap=120,
    )
    assert reingested == 45
    info_after = memory_qdrant.get_collection(col_name)
    assert info_after.points_count == 45, "Re-ingesting must not duplicate points"

    # 3. Add a 12th article (single chunk) -> point count increments from 45 to 46
    new_article = Article(
        article_number="KB0099",
        version="1.0",
        title="Payment gateway connection timeout",
        short_description="Troubleshoot payment gateway timeout incidents.",
        category="software",
        service="order-processing",
        workflow_state=WorkflowState.PUBLISHED,
        security_level=SecurityLevel.RESTRICTED,
        body=(
            "# Payment gateway connection timeout\n\n"
            "Payment API requests fail with 504 Gateway Timeout due to downstream latency. "
            "Verify health dashboard and toggle failover circuit if needed."
        ),
    )
    added_count = ingest_articles(
        articles=[new_article],
        client=memory_qdrant,
        collection_name=col_name,
        embedding_engine=mock_engine,
        chunk_size=700,
        chunk_overlap=120,
    )
    assert added_count == 1
    info_new = memory_qdrant.get_collection(col_name)
    assert info_new.points_count == 46


def test_shim_matches() -> None:
    assert shim_ingest is ingest_articles
