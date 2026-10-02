"""Tests for citation building and the manual-section mapping."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from app.chat.citations import (
    Citation,
    build_citation,
    chunk_identity,
    citation_label,
    manual_section_for_article_number,
)
from app.retrieval.hybrid_search import RetrievalHit

_CORPUS = Path("data/corpus/manual_semantic_sections.json")


def _load_forward_mapper():
    """Import the ingestion pipeline's article_number_for_section directly."""
    spec = importlib.util.spec_from_file_location(
        "manual_pipeline_semantic_ingest",
        "scripts/manual/pipeline_semantic_ingest.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.article_number_for_section


@pytest.mark.parametrize(
    ("article_number", "expected"),
    [
        ("KB0000", "Document control"),
        ("KB0304", "3.4"),
        ("KB0600", "6"),
        ("KB0613", "6.13"),
        ("KB1300", "A"),
        ("KB1403", "B.3"),
        ("KB0010", None),  # canonical incident article: no manual section
        ("KB0100", "1"),
        ("KB9999", None),  # beyond Appendix Z: not a valid section number
        ("", None),
        ("INC001", None),
    ],
)
def test_manual_section_mapping(article_number: str, expected: str | None) -> None:
    assert manual_section_for_article_number(article_number) == expected


@pytest.mark.skipif(not _CORPUS.exists(), reason="manual corpus not present")
def test_mapping_round_trips_the_whole_manual_corpus() -> None:
    """The inverse mapping is checked against the real corpus, not assumed."""
    forward = _load_forward_mapper()
    sections = json.loads(_CORPUS.read_text(encoding="utf-8"))["sections"]
    checked = 0
    for item in sections:
        section_number = str(item["section_number"])
        article_number = forward(section_number)
        assert manual_section_for_article_number(article_number) == section_number, (
            f"{article_number} must map back to §{section_number}"
        )
        checked += 1
    assert checked > 50


def _hit(**overrides: object) -> RetrievalHit:
    values: dict = {
        "score": 0.87,
        "article_id": "KB0704-v1.0",
        "article_number": "KB0704",
        "version": "1.0",
        "title": "Three symptoms, one cause",
        "section": "General",
        "chunk_index": 0,
        "chunk_text": "The known error register links every related incident.",
        "workflow_state": "published",
        "security_level": "restricted",
        "category": "process",
        "service": "general",
    }
    values.update(overrides)
    return RetrievalHit.model_validate(values)


def test_build_citation_for_manual_article() -> None:
    citation = build_citation(_hit())

    assert isinstance(citation, Citation)
    assert citation.manual_section == "7.4"
    assert citation.excerpt.startswith("The known error register")
    assert citation.article_url is None, "no source-link mapping exists; never fabricate one"
    assert citation_label(citation) == ("KB0704 — Manual §7.4 — Three symptoms, one cause")


def test_build_citation_for_canonical_article_has_no_manual_section() -> None:
    citation = build_citation(
        _hit(
            article_id="KB0005-v2.0",
            article_number="KB0005",
            version="2.0",
            title="Account is locked",
            section="Resolution",
            category="inquiry",
            service=None,
        )
    )

    assert citation.manual_section is None
    assert citation_label(citation) == "KB0005 v2.0 — Account is locked — §Resolution"


def test_build_citation_truncates_long_excerpts() -> None:
    citation = build_citation(_hit(chunk_text="x" * 1000))

    assert len(citation.excerpt) < 700
    assert citation.excerpt.endswith("…[truncated]")


def test_chunk_identity_matches_ingest_point_composition() -> None:
    assert chunk_identity("KB0704-v1.0", 3) == "KB0704-v1.0::chunk::3"
