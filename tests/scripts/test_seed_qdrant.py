"""Seed-safety contract: seeding the corpus must never destroy other knowledge.

The seed command is the collection's bulk writer, so its contract is strict:
articles in the corpus are replaced exactly, and everything else already in
the collection — human-captured KB articles, non-article records — survives
and does not break completion. Verification is by expected point identity,
not by whole-collection counts.
"""

import importlib.util
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct, SparseVector

from app.clients.qdrant import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME, ensure_collection
from app.models.knowledge import Article, SecurityLevel, WorkflowState
from app.retrieval.embedding import EmbeddedText
from app.retrieval.ingest import ingest_articles

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "seed_qdrant.py"
COLLECTION = "seed_safety_test"


def _load_module():
    spec = importlib.util.spec_from_file_location("seed_qdrant_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mock_engine() -> MagicMock:
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


def _corpus_article(number: str, sections: int = 2) -> Article:
    filler = "Follow the operational procedure step as documented. " * 12
    body = "\n\n".join(f"## Section {i}\n\n{filler}" for i in range(sections))
    return Article(
        article_number=number,
        version="1.0",
        title=f"Procedure article {number}",
        short_description=f"Seed-safety fixture article {number}.",
        category="software",
        service="order-processing",
        workflow_state=WorkflowState.PUBLISHED,
        security_level=SecurityLevel.INTERNAL,
        body=body,
    )


def _human_captured_article(number: str) -> Article:
    article = _corpus_article(number, sections=1)
    return article.model_copy(update={"workflow_state": WorkflowState.HUMAN_RESOLVED})


def _write_corpus(tmp_path: Path, articles: list[Article]) -> Path:
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps([article.model_dump() for article in articles]))
    return path


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


def _run_seed(
    monkeypatch: pytest.MonkeyPatch,
    client: QdrantClient,
    corpus: Path,
    extra_args: list[str] | None = None,
    module=None,
) -> int:
    if module is None:
        module = _load_module()
    monkeypatch.setattr(module, "QdrantClient", lambda url: client)
    monkeypatch.setattr(module, "FastEmbedEngine", lambda: _mock_engine())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "seed_qdrant.py",
            "--corpus",
            str(corpus),
            "--collection",
            COLLECTION,
            "--url",
            "http://localhost:6333",
            "--no-stressors",
            *(extra_args or []),
        ],
    )
    return module.main()


def test_seed_preserves_human_captured_articles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seeding the corpus must not delete a live human-captured KB article."""
    client = QdrantClient(":memory:")
    captured = _human_captured_article("KB1002")
    ingest_articles([captured], client, COLLECTION, _mock_engine())
    assert len(_points_for_article(client, "KB1002-v1.0")) == 1

    corpus = _write_corpus(tmp_path, [_corpus_article("KB0001")])
    exit_code = _run_seed(monkeypatch, client, corpus)

    assert exit_code == 0
    survivors = _points_for_article(client, "KB1002-v1.0")
    assert len(survivors) == 1, "seed purged the human-captured article"
    assert survivors[0].payload["chunk_text"]


def test_seed_succeeds_with_untracked_points_in_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-existing non-article records neither break seeding nor get deleted."""
    client = QdrantClient(":memory:")
    ensure_collection(client, COLLECTION, dense_vector_size=384)
    legacy_point = PointStruct(
        id=str(uuid.uuid4()),
        vector={
            DENSE_VECTOR_NAME: [0.1] * 384,
            SPARSE_VECTOR_NAME: SparseVector(indices=[1, 2], values=[0.5, 0.8]),
        },
        payload={"note": "pre-existing non-article record"},
    )
    client.upsert(collection_name=COLLECTION, points=[legacy_point], wait=True)

    corpus = _write_corpus(tmp_path, [_corpus_article("KB0001")])
    exit_code = _run_seed(monkeypatch, client, corpus)

    assert exit_code == 0
    stored, _ = client.scroll(collection_name=COLLECTION, limit=100, with_payload=True)
    assert any(point.id == legacy_point.id for point in stored), "legacy record was deleted"
    assert len(_points_for_article(client, "KB0001-v1.0")) == 2


def test_seed_does_not_auto_ingest_manual_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manual artifact existing on disk must not trigger seeding by itself.

    Raw manual sections are a scratch-format staging corpus: they are ingested
    only when an explicit separate target is requested, never as a silent side
    effect of an ordinary article seed.
    """
    monkeypatch.chdir(tmp_path)
    manual = tmp_path / "data" / "corpus" / "manual_sections.json"
    manual.parent.mkdir(parents=True)
    manual.write_text("{}")

    corpus = _write_corpus(tmp_path, [_corpus_article("KB0001")])
    exit_code = _run_seed(monkeypatch, QdrantClient(":memory:"), corpus)

    assert exit_code == 0


def test_seed_manual_rejects_colocated_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manual sections must never be ingested into the article collection."""
    client = QdrantClient(":memory:")
    corpus = _write_corpus(tmp_path, [_corpus_article("KB0001")])
    manual = tmp_path / "manual_sections.json"
    manual.write_text("{}")

    exit_code = _run_seed(
        monkeypatch,
        client,
        corpus,
        extra_args=["--manual-corpus", str(manual), "--manual-collection", COLLECTION],
    )

    assert exit_code == 1


def test_seed_manual_uses_chunked_sections_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manual ingestion goes load_sections → chunk_sections → ingest(chunks=…)."""
    module = _load_module()
    section, relationships = MagicMock(name="section"), MagicMock(name="relationships")
    chunk = MagicMock(name="chunk")
    source = MagicMock()
    source.load_sections.return_value = ([section], relationships)
    monkeypatch.setattr(module, "ManualCorpusJSONSource", lambda path: source)
    monkeypatch.setattr(module, "chunk_sections", lambda sections, rels: [chunk])
    ingest = MagicMock(return_value=3)
    monkeypatch.setattr(module, "ingest_manual_sections", ingest)

    manual = tmp_path / "manual_sections.json"
    manual.write_text("{}")
    corpus = _write_corpus(tmp_path, [_corpus_article("KB0001")])

    exit_code = _run_seed(
        monkeypatch,
        QdrantClient(":memory:"),
        corpus,
        extra_args=["--manual-corpus", str(manual), "--manual-collection", "manual_scratch"],
        module=module,
    )

    assert exit_code == 0
    ingest.assert_called_once()
    kwargs = ingest.call_args.kwargs
    assert kwargs["chunks"] == [chunk]
    assert kwargs["collection_name"] == "manual_scratch"
    assert "sections" not in kwargs and "relationships" not in kwargs
