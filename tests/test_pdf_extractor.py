"""Tests for the PDF knowledge extractor."""

from pathlib import Path

import pytest

from app.models.knowledge import Article, SecurityLevel, WorkflowState
from app.retrieval.pdf_extractor import PDFKnowledgeExtractor

PDF_PATH = Path("data/barq-system-kb.pdf")


@pytest.fixture
def extractor() -> PDFKnowledgeExtractor:
    return PDFKnowledgeExtractor(PDF_PATH)


def test_pdf_file_exists(extractor: PDFKnowledgeExtractor) -> None:
    assert extractor.pdf_path.exists(), f"PDF must exist at {extractor.pdf_path}"


def test_extract_all_articles(extractor: PDFKnowledgeExtractor) -> None:
    articles = extractor.extract_articles()
    # 9 published standard articles + 2 versions of KB0010 = 11 articles
    assert len(articles) == 11
    assert all(isinstance(a, Article) for a in articles)


def test_article_ids_and_versions(extractor: PDFKnowledgeExtractor) -> None:
    articles = extractor.extract_articles()
    ids = [a.article_id for a in articles]
    assert "KB0001-v2.0" in ids
    assert "KB0002-v3.0" in ids
    assert "KB0003-v2.0" in ids
    assert "KB0004-v1.0" in ids
    assert "KB0005-v4.0" in ids
    assert "KB0006-v3.0" in ids
    assert "KB0007-v2.0" in ids
    assert "KB0008-v1.0" in ids
    assert "KB0009-v2.0" in ids
    assert "KB0010-v1.0" in ids
    assert "KB0010-v2.0" in ids


def test_security_level_mapping_rules(extractor: PDFKnowledgeExtractor) -> None:
    articles = {a.article_number: a for a in extractor.extract_articles()}

    # End-user desk cards -> internal
    assert articles["KB0001"].security_level == SecurityLevel.INTERNAL
    assert articles["KB0002"].security_level == SecurityLevel.INTERNAL
    assert articles["KB0003"].security_level == SecurityLevel.INTERNAL
    assert articles["KB0004"].security_level == SecurityLevel.INTERNAL
    assert articles["KB0005"].security_level == SecurityLevel.INTERNAL
    assert articles["KB0006"].security_level == SecurityLevel.INTERNAL
    assert articles["KB0009"].security_level == SecurityLevel.INTERNAL

    # Sensitive infrastructure & platform runbooks -> restricted
    assert articles["KB0007"].security_level == SecurityLevel.RESTRICTED
    assert articles["KB0008"].security_level == SecurityLevel.RESTRICTED
    assert articles["KB0010"].security_level == SecurityLevel.RESTRICTED


def test_kb0010_lifecycle_states(extractor: PDFKnowledgeExtractor) -> None:
    articles = [a for a in extractor.extract_articles() if a.article_number == "KB0010"]
    assert len(articles) == 2

    by_ver = {a.version: a for a in articles}
    assert by_ver["1.0"].workflow_state == WorkflowState.RETIRED
    assert by_ver["2.0"].workflow_state == WorkflowState.PUBLISHED


def test_markdown_structure_contains_standard_sections(
    extractor: PDFKnowledgeExtractor,
) -> None:
    articles = extractor.extract_articles()
    for art in articles:
        assert "## Symptom" in art.body, f"{art.article_id} missing ## Symptom"
        assert "## Cause" in art.body, f"{art.article_id} missing ## Cause"
        assert "## Resolution" in art.body, f"{art.article_id} missing ## Resolution"
        assert "## Escalation" in art.body, f"{art.article_id} missing ## Escalation"
        assert len(art.short_description) <= 255


def test_save_corpus_writes_valid_json(extractor: PDFKnowledgeExtractor, tmp_path: Path) -> None:
    out_file = tmp_path / "barq_articles.json"
    saved = extractor.save_corpus(out_file)
    assert saved.exists()
    content = saved.read_text(encoding="utf-8")
    assert "KB0001-v2.0" in content
    assert "KB0010-v2.0" in content
