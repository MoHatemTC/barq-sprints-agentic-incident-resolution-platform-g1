"""Contract for the post-ingestion verifier: expected points present and intact.

Replaces the seed script's whole-collection count check. A collection that
also holds human-captured or other non-seed records can never satisfy a count
equality, and a count can pass while an expected chunk is missing. The
verifier checks the seeded corpus by deterministic point ID and chunk
content, and ignores everything else in the collection.
"""

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue, PointIdsList

from app.models.knowledge import Article, SecurityLevel, WorkflowState
from app.retrieval.ingest import ingest_articles
from app.retrieval.verification import SeedVerificationError, verify_seeded_articles

COLLECTION = "verification_test"


@pytest.fixture
def memory_qdrant() -> QdrantClient:
    return QdrantClient(":memory:")


def _article(number: str, sections: int) -> Article:
    filler = "Follow the operational procedure step as documented. " * 12
    body = "\n\n".join(f"## Section {i}\n\n{filler}" for i in range(sections))
    return Article(
        article_number=number,
        version="1.0",
        title=f"Procedure article {number}",
        short_description=f"Verifier fixture article {number}.",
        category="software",
        service="order-processing",
        workflow_state=WorkflowState.PUBLISHED,
        security_level=SecurityLevel.INTERNAL,
        body=body,
    )


def _mock_engine():
    from unittest.mock import MagicMock

    from app.retrieval.embedding import EmbeddedText

    engine = MagicMock()
    engine.dense_vector_size = 384
    engine.embed_documents.side_effect = lambda docs: [
        EmbeddedText(
            dense=[0.1] * 384,
            sparse_indices=[1, 2],
            sparse_values=[0.5, 0.8],
        )
        for _ in docs
    ]
    return engine


def _points_for_article(client: QdrantClient, article_id: str) -> list:
    points, _ = client.scroll(
        collection_name=COLLECTION,
        limit=100,
        scroll_filter=Filter(
            must=[FieldCondition(key="article_id", match=MatchValue(value=article_id))]
        ),
        with_payload=True,
    )
    return points


def test_missing_chunk_fails_verification(memory_qdrant: QdrantClient) -> None:
    article = _article("KB0001", sections=2)
    ingest_articles([article], memory_qdrant, COLLECTION, _mock_engine())
    stored = _points_for_article(memory_qdrant, article.article_id)
    assert len(stored) == 2

    victim = stored[0]
    memory_qdrant.delete(
        collection_name=COLLECTION,
        points_selector=PointIdsList(points=[victim.id]),
        wait=True,
    )

    with pytest.raises(SeedVerificationError, match="KB0001-v1.0"):
        verify_seeded_articles(memory_qdrant, COLLECTION, [article])


def test_tampered_chunk_content_fails_verification(memory_qdrant: QdrantClient) -> None:
    article = _article("KB0001", sections=1)
    ingest_articles([article], memory_qdrant, COLLECTION, _mock_engine())
    stored = _points_for_article(memory_qdrant, article.article_id)
    assert len(stored) == 1

    memory_qdrant.set_payload(
        collection_name=COLLECTION,
        payload={"chunk_text": "TAMPERED — do not restart the application server."},
        points=[stored[0].id],
        wait=True,
    )

    with pytest.raises(SeedVerificationError, match="content"):
        verify_seeded_articles(memory_qdrant, COLLECTION, [article])


def test_unrelated_records_are_ignored_not_errors(memory_qdrant: QdrantClient) -> None:
    seeded = _article("KB0001", sections=2)
    other = _article("KB1002", sections=1)
    ingest_articles([seeded, other], memory_qdrant, COLLECTION, _mock_engine())

    # The verifier only answers for the seeded corpus; other live records in
    # the same collection (e.g. human-captured articles) are not its business.
    verify_seeded_articles(memory_qdrant, COLLECTION, [seeded])
