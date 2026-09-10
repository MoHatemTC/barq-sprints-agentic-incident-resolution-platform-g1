"""Automated validation tests for the real BARQ knowledge corpus and ground truth matrix."""

import csv
from pathlib import Path

from app.models.knowledge import Article, SecurityLevel, WorkflowState
from app.retrieval.sources import LocalJSONSource

CORPUS_PATH = Path("data/corpus/barq_articles.json")
COVERAGE_PATH = Path("data/coverage_matrix.csv")


def test_corpus_file_exists_and_loads_all_articles() -> None:
    assert CORPUS_PATH.exists(), f"{CORPUS_PATH} must exist"
    source = LocalJSONSource(CORPUS_PATH)
    articles = source.load_articles()
    msg = f"Expected exactly 11 articles (KB0001-KB0010 + retired v1), got {len(articles)}"
    assert len(articles) == 11, msg
    assert all(isinstance(a, Article) for a in articles)


def test_all_article_ids_are_unique() -> None:
    articles = LocalJSONSource(CORPUS_PATH).load_articles()
    ids = [a.unique_key for a in articles]
    assert len(ids) == len(set(ids)), f"Duplicate unique_keys found in corpus: {ids}"
    assert len(ids) == 11


def test_mandatory_metadata_fields_are_populated() -> None:
    articles = LocalJSONSource(CORPUS_PATH).load_articles()
    for art in articles:
        assert art.article_number, "Missing article_number"
        assert art.version, f"{art.article_number} missing version"
        assert art.title, f"{art.article_number} missing title"
        assert art.body, f"{art.article_number} missing body"
        assert art.category, f"{art.article_number} missing category"
        assert art.service, f"{art.article_number} missing service"
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
        assert art.short_description, f"{art.article_number} missing short_description"
        assert len(art.short_description) <= 255


def test_security_level_mapping_rules() -> None:
    """Verify tier mapping: print/endpoint/sap/order are restricted; helpdesk is internal."""
    articles = LocalJSONSource(CORPUS_PATH).load_articles()
    restricted_numbers = {"KB0004", "KB0007", "KB0008", "KB0010"}
    internal_numbers = {"KB0001", "KB0002", "KB0003", "KB0005", "KB0006", "KB0009"}
    for a in articles:
        if a.article_number in restricted_numbers:
            err = f"{a.article_number} should have restricted security level"
            assert a.security_level == SecurityLevel.RESTRICTED, err
        elif a.article_number in internal_numbers:
            err = f"{a.article_number} should have internal security level"
            assert a.security_level == SecurityLevel.INTERNAL, err


def test_markdown_structure_contains_standard_sections() -> None:
    """Every article body must contain the four standard operational markdown sections."""
    articles = LocalJSONSource(CORPUS_PATH).load_articles()
    required_headings = ["## Symptom", "## Cause", "## Resolution", "## Escalation"]
    for a in articles:
        for heading in required_headings:
            assert heading in a.body, f"{a.unique_key} missing {heading} section"


def test_version_disambiguation_pairs_exist() -> None:
    articles = LocalJSONSource(CORPUS_PATH).load_articles()
    by_number: dict[str, dict[str, WorkflowState]] = {}
    for a in articles:
        by_number.setdefault(a.article_number, {})[a.version] = a.workflow_state

    # KB0010 has v1.0 (retired) and v2.0 (published)
    assert "KB0010" in by_number
    assert "1.0" in by_number["KB0010"]
    assert "2.0" in by_number["KB0010"]
    assert by_number["KB0010"]["1.0"] == WorkflowState.RETIRED
    assert by_number["KB0010"]["2.0"] == WorkflowState.PUBLISHED


def test_lifecycle_states_include_published_and_retired() -> None:
    articles = LocalJSONSource(CORPUS_PATH).load_articles()
    states = {a.workflow_state for a in articles}
    assert WorkflowState.PUBLISHED in states
    assert WorkflowState.RETIRED in states


def test_coverage_matrix_integrity() -> None:
    assert COVERAGE_PATH.exists(), f"{COVERAGE_PATH} must exist"
    articles = LocalJSONSource(CORPUS_PATH).load_articles()
    valid_ids = {a.article_number for a in articles} | {a.unique_key for a in articles}

    with COVERAGE_PATH.open(encoding="utf-8") as f:
        lines = [line for line in f if not line.strip().startswith("#")]
        rows = list(csv.DictReader(lines))

    # 13 real incidents from the manual (4 Section 7 + 8 Section 6 + 1 Section 9.1)
    assert len(rows) == 13, f"Expected exactly 13 active incidents from manual, got {len(rows)}"

    incident_map = {row["incident_id"]: row for row in rows}

    # Verify Section 7 core benchmark incidents from manual exist
    assert "INC0010023" in incident_map  # VPN auth failure (7.2)
    assert "INC0010047" in incident_map  # Printer noise unanswerable (7.3)
    assert "INC0010064" in incident_map  # Account lockout (7.4)
    assert "INC0010052" in incident_map  # Order service pool exhaustion (7.5)
    assert "INC0009884" in incident_map  # Major incident pool restart outage (9.1)

    unanswerable_count = 0

    for row in rows:
        inc_id = row["incident_id"]
        if row["is_answerable"] == "true":
            p_ids = [x for x in row["primary_article_ids"].split(";") if x]
            assert p_ids, f"{inc_id} is answerable but has no primary_article_ids"
            for aid in p_ids:
                assert aid in valid_ids, f"Unknown primary_article_id {aid!r} in {inc_id}"

            if row["acceptable_article_ids"]:
                for aid in row["acceptable_article_ids"].split(";"):
                    if aid:
                        err = f"Unknown acceptable_article_id {aid!r} in {inc_id}"
                        assert aid in valid_ids, err
        else:
            unanswerable_count += 1
            assert not row["primary_article_ids"], f"Unanswerable {inc_id} has primary IDs"

    msg = f"Expected at least 1 unanswerable case, got {unanswerable_count}"
    assert unanswerable_count >= 1, msg
