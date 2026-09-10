"""Automated validation tests for the 27-article knowledge corpus and ground truth matrix."""

import csv
from pathlib import Path

from app.models.knowledge import Article, SecurityLevel, WorkflowState
from app.retrieval.sources import LocalJSONSource

CORPUS_PATH = Path("data/corpus/articles.json")
COVERAGE_PATH = Path("data/coverage_matrix.csv")


def test_corpus_file_exists_and_loads_all_articles() -> None:
    assert CORPUS_PATH.exists(), f"{CORPUS_PATH} must exist"
    source = LocalJSONSource(CORPUS_PATH)
    articles = source.load_articles()
    assert len(articles) >= 25, f"Expected at least 25 articles, got {len(articles)}"
    assert all(isinstance(a, Article) for a in articles)


def test_all_article_ids_are_unique() -> None:
    articles = LocalJSONSource(CORPUS_PATH).load_articles()
    ids = [a.article_id for a in articles]
    assert len(ids) == len(set(ids)), "Duplicate article_ids found in corpus"


def test_mandatory_metadata_fields_are_populated() -> None:
    articles = LocalJSONSource(CORPUS_PATH).load_articles()
    for art in articles:
        assert art.category, f"{art.article_id} missing category"
        assert art.service, f"{art.article_id} missing service"
        assert art.workflow_state in (
            WorkflowState.PUBLISHED,
            WorkflowState.DRAFT,
            WorkflowState.RETIRED,
        )
        assert art.security_level in (
            SecurityLevel.PUBLIC,
            SecurityLevel.INTERNAL,
            SecurityLevel.RESTRICTED,
        )
        assert art.version, f"{art.article_id} missing version"
        assert len(art.short_description) <= 255


def test_version_disambiguation_pairs_exist() -> None:
    articles = LocalJSONSource(CORPUS_PATH).load_articles()
    by_base: dict[str, list[str]] = {}
    for a in articles:
        by_base.setdefault(a.base_id, []).append(a.version)

    # PostgreSQL 14 vs 16
    assert "KB-DB-001" in by_base
    assert "1.0" in by_base["KB-DB-001"]
    assert "2.0" in by_base["KB-DB-001"]

    # NGINX 502 vs 504
    assert "KB-NET-001" in by_base
    assert "1.0" in by_base["KB-NET-001"]
    assert "2.0" in by_base["KB-NET-001"]


def test_lifecycle_states_include_draft_and_retired() -> None:
    articles = LocalJSONSource(CORPUS_PATH).load_articles()
    states = {a.workflow_state for a in articles}
    assert WorkflowState.PUBLISHED in states
    assert WorkflowState.DRAFT in states
    assert WorkflowState.RETIRED in states


def test_coverage_matrix_integrity() -> None:
    assert COVERAGE_PATH.exists(), f"{COVERAGE_PATH} must exist"
    corpus_ids = {a.article_id for a in LocalJSONSource(CORPUS_PATH).load_articles()}

    with COVERAGE_PATH.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) >= 30, f"Expected at least 30 coverage test cases, got {len(rows)}"

    multi_article_count = 0
    unanswerable_count = 0

    for row in rows:
        inc_id = row["incident_id"]
        if row["is_answerable"] == "true":
            p_ids = [x for x in row["primary_article_ids"].split(";") if x]
            assert p_ids, f"{inc_id} is answerable but has no primary_article_ids"
            for aid in p_ids:
                assert aid in corpus_ids, f"Unknown primary_article_id {aid!r} in {inc_id}"

            if len(p_ids) > 1:
                multi_article_count += 1
        else:
            unanswerable_count += 1
            assert not row["primary_article_ids"], f"Unanswerable {inc_id} has primary IDs"

    assert multi_article_count >= 5, f"Expected >= 5 multi-article cases, got {multi_article_count}"
    assert unanswerable_count >= 3, f"Expected >= 3 unanswerable cases, got {unanswerable_count}"
