"""Manual publication integration: manifest output flows through the standard
article ingestion path with provenance surviving into chunk payloads.

The adapter emits ordinary Articles, so ingestion is the existing
``ingest_articles`` — these tests pin that the combined run is what lands in
Qdrant: KB2xxx and alias points carry content_purpose/warning/source_sections,
and a provenance-free ingest stays byte-identical to the legacy payload shape.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

from qdrant_client import QdrantClient

from app.models.knowledge import Article, KnowledgePayload
from app.retrieval.chunking import chunk_article
from app.retrieval.embedding import EmbeddedText
from app.retrieval.ingest import ingest_articles
from app.retrieval.manual.integration import load_manual_publication

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / "data" / "corpus" / "manual_kb_manifest.json"
SECTIONS = REPO_ROOT / "data" / "corpus" / "manual_sections.json"
CORPUS = REPO_ROOT / "data" / "corpus" / "barq_articles.json"
COLLECTION = "manual_integration_test"


def _mock_engine() -> MagicMock:
    engine = MagicMock()
    engine.dense_vector_size = 384
    engine.embed_documents.side_effect = lambda docs: [
        EmbeddedText(dense=[0.1] * 384, sparse_indices=[1, 2], sparse_values=[0.5, 0.8])
        for _ in docs
    ]
    return engine


def _points_by_article(client: QdrantClient) -> dict:
    points, _ = client.scroll(collection_name=COLLECTION, with_payload=True, limit=1000)
    return {p.payload["article_id"]: p.payload for p in points}


def test_publication_units_ingest_with_provenance_payloads() -> None:
    client = QdrantClient(":memory:")
    publication = load_manual_publication(MANIFEST, SECTIONS, CORPUS)
    assert len(publication.articles) == 75
    assert set(publication.provenance) == {a.article_id for a in publication.articles}

    ingest_articles(
        articles=publication.articles,
        client=client,
        collection_name=COLLECTION,
        embedding_engine=_mock_engine(),
        article_provenance=publication.provenance,
    )
    payloads = _points_by_article(client)
    assert len(payloads) == len(publication.provenance)

    warning = payloads["KB2065-v1.0"]
    assert warning["content_purpose"] == "warning"
    assert warning["warning"]
    assert warning["security_level"] == "restricted"

    historical = payloads["KB2034-v1.0"]
    assert historical["content_purpose"] == "historical"
    assert "KB0010 version 1.0" in historical["warning"]
    assert "not an executable procedure" in historical["warning"]

    alias = payloads["KB0001-v2.0"]
    assert alias["content_purpose"] == "current_procedure"
    assert alias["source_sections"] == ["6.4"]
    assert alias["unit_id"] == "section-6.4"


def test_provenance_free_ingest_keeps_legacy_payload_shape() -> None:
    """C1 guard: a corpus-only ingest must not grow provenance fields."""
    corpus = [Article.model_validate(item) for item in json.loads(CORPUS.read_text())]
    article = corpus[0]
    chunk = next(iter(chunk_article(article)))
    payload = KnowledgePayload.from_chunk(article, chunk).to_qdrant_payload()
    for field in ("content_purpose", "warning", "source_sections", "unit_id"):
        assert field not in payload, f"legacy payload grew {field}"


def test_manual_point_ids_are_deterministic() -> None:
    publication = load_manual_publication(MANIFEST, SECTIONS, CORPUS)
    from app.retrieval.ingest import build_point_id

    first = {a.article_id: build_point_id(a.article_id, 0) for a in publication.articles}
    second_run = load_manual_publication(MANIFEST, SECTIONS, CORPUS)
    second = {a.article_id: build_point_id(a.article_id, 0) for a in second_run.articles}
    assert first == second
