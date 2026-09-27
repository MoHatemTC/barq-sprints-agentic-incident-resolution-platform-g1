"""Manual evidence through the real incident retriever.

Three contracts from plan step 7: labels with no legacy category (security,
other) still get the authorized wide search instead of an empty result, the
hybrid ranker's fused order survives the category/wide merge (dense cosine
calibrates sufficiency, it does not re-sort), and payload provenance reaches
EvidenceItem.
"""

from pathlib import Path
from unittest.mock import MagicMock

from qdrant_client import QdrantClient

from agent.retrieval import QdrantRetriever
from agent.state import EvidenceItem
from app.models.knowledge import Classification
from app.retrieval.embedding import EmbeddedText
from app.retrieval.ingest import ingest_articles
from app.retrieval.manual.integration import load_manual_publication

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "data" / "corpus" / "manual_kb_manifest.json"
SECTIONS = REPO_ROOT / "data" / "corpus" / "manual_sections.json"
CORPUS = REPO_ROOT / "data" / "corpus" / "barq_articles.json"
COLLECTION = "agent_manual_retrieval_test"


def _mock_engine() -> MagicMock:
    engine = MagicMock()
    engine.dense_vector_size = 384
    engine.embed_documents.side_effect = lambda docs: [
        EmbeddedText(dense=[0.1] * 384, sparse_indices=[1, 2], sparse_values=[0.5, 0.8])
        for _ in docs
    ]
    engine.embed_query.return_value = EmbeddedText(
        dense=[0.1] * 384, sparse_indices=[1, 2], sparse_values=[0.5, 0.8]
    )
    return engine


def _seeded_retriever() -> QdrantRetriever:
    client = QdrantClient(":memory:")
    publication = load_manual_publication(MANIFEST, SECTIONS, CORPUS)
    ingest_articles(
        articles=publication.articles,
        client=client,
        collection_name=COLLECTION,
        embedding_engine=_mock_engine(),
        article_provenance=publication.provenance,
    )
    return QdrantRetriever(
        lambda: client,
        _mock_engine,
        collection_name=COLLECTION,
    )


def test_uncategorized_labels_still_get_the_authorized_wide_search() -> None:
    result = _seeded_retriever().search(
        "escalation policy for a flagged pilot suggestion",
        classification=Classification.SECURITY,
        top_k=5,
        threshold=0.55,
    )
    assert result.hits, "security-labelled incidents must still reach the manual corpus"
    assert result.category_filter is None
    restricted = {"KB0004", "KB0007", "KB0008", "KB0010", "KB2065"}
    returned = {hit.article_number for hit in result.hits}
    assert not returned & restricted, "the mandatory security filter must still hold"


def test_provenance_flows_into_evidence_items() -> None:
    result = _seeded_retriever().search(
        "symptom finder and the article index",
        classification=Classification.OTHER,
        top_k=10,
        threshold=0.55,
    )
    with_purpose = [h for h in result.hits if h.content_purpose]
    assert with_purpose, "manual hits must carry content_purpose"
    assert any(h.source_sections for h in with_purpose)
    warning_hit = next((h for h in result.hits if h.article_number == "KB2065"), None)
    if warning_hit is not None:
        assert warning_hit.content_purpose == "warning"
        assert warning_hit.warning


def test_fused_ranking_survives_the_merge(monkeypatch) -> None:
    retriever = _seeded_retriever()

    item_a = EvidenceItem(
        article_id="KB0001-v2.0",
        article_number="KB0001",
        version="2.0",
        title="A",
        section="S",
        chunk_index=0,
        text="a",
        fused_score=0.9,
        relevance=0.60,
    )
    item_b = EvidenceItem(
        article_id="KB0002-v3.0",
        article_number="KB0002",
        version="3.0",
        title="B",
        section="S",
        chunk_index=0,
        text="b",
        fused_score=0.8,
        relevance=0.90,
    )
    monkeypatch.setattr(retriever, "_one_pass", lambda *a, **k: ([item_a, item_b], 0.9))
    monkeypatch.setattr(
        retriever,
        "_dense_scores",
        lambda *a, **k: {(item_a.article_id, 0): 0.60, (item_b.article_id, 0): 0.90},
    )

    result = retriever.search(
        "vpn", classification=Classification.HARDWARE, top_k=5, threshold=0.55
    )
    assert [h.article_number for h in result.hits] == ["KB0001", "KB0002"], (
        "the ranker's fused order must not be re-sorted by dense cosine"
    )
    assert result.sufficient, "relevance still gates sufficiency (B is 0.90)"
    assert result.best_relevance == 0.90
