"""Automated validation tests for the real BARQ knowledge corpus and ground truth matrix.

These tests assert invariants of the REAL corpus (data/corpus/barq_articles.json),
which is git-ignored because the source manual is INTERNAL-marked. On machines
without the file (fresh clones, CI), the module skips as a whole — run
`uv run python scripts/extract_barq_kb.py` locally against the PDF to enable it.
"""

import csv
from pathlib import Path

import pytest

from app.models.knowledge import Article, SecurityLevel, WorkflowState
from app.retrieval.sources import LocalJSONSource

CORPUS_PATH = Path("data/corpus/barq_articles.json")
COVERAGE_PATH = Path("data/coverage_matrix.csv")

pytestmark = pytest.mark.skipif(
    not CORPUS_PATH.exists(),
    reason="real corpus not available (git-ignored INTERNAL data; extract locally from the PDF)",
)


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


def test_coverage_matrix_is_machine_readable() -> None:
    """S4.4's harness reads this file with plain csv.DictReader — no comment stripping.

    Regression guard for the P2 review finding: any `#` line (section header or
    commented scenario) comes back as a junk incident row in pandas/Excel.
    """
    with COVERAGE_PATH.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 25, f"Expected exactly 25 scenarios, got {len(rows)}"
    bad = [r["incident_id"] for r in rows if not r["incident_id"].startswith("INC00")]
    assert not bad, f"Non-incident rows visible to a standard CSV reader: {bad}"


def test_coverage_matrix_integrity() -> None:
    assert COVERAGE_PATH.exists(), f"{COVERAGE_PATH} must exist"
    articles = LocalJSONSource(CORPUS_PATH).load_articles()
    valid_ids = {a.article_number for a in articles} | {a.unique_key for a in articles}

    # Plain DictReader — the file itself must be clean, no comment filtering.
    with COVERAGE_PATH.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 25, f"Expected exactly 25 scenarios, got {len(rows)}"
    sources = {r["source"] for r in rows}
    assert sources == {"manual", "synthetic"}, f"Unexpected sources: {sources}"
    assert sum(r["source"] == "manual" for r in rows) == 13
    assert sum(r["source"] == "synthetic" for r in rows) == 12

    incident_map = {row["incident_id"]: row for row in rows}

    # Verify Section 7 core benchmark incidents from manual exist
    assert "INC0010023" in incident_map  # VPN auth failure (7.2)
    assert "INC0010047" in incident_map  # Printer noise unanswerable (7.3)
    assert "INC0010064" in incident_map  # Account lockout (7.4)
    assert "INC0010052" in incident_map  # Order service pool exhaustion (7.5)
    assert "INC0009884" in incident_map  # Major incident pool restart outage (9.1)

    unanswerable_count = 0
    multi_article_count = 0

    for row in rows:
        inc_id = row["incident_id"]
        assert row["source"] in ("manual", "synthetic"), f"Bad source in {inc_id}"

        primary = [x for x in row["primary_article_ids"].split(";") if x]
        acceptable = [x for x in row["acceptable_article_ids"].split(";") if x]
        forbidden = [x for x in row["forbidden_article_ids"].split(";") if x]

        if row["is_answerable"] == "true":
            assert primary, f"{inc_id} is answerable but has no primary_article_ids"
            if len(primary) > 1:
                multi_article_count += 1
            for aid in primary:
                assert aid in valid_ids, f"Unknown primary_article_id {aid!r} in {inc_id}"
            for aid in acceptable:
                assert aid in valid_ids, f"Unknown acceptable_article_id {aid!r} in {inc_id}"
        else:
            unanswerable_count += 1
            assert not primary, f"Unanswerable {inc_id} has primary IDs"

        for aid in forbidden:
            assert aid in valid_ids, f"Unknown forbidden_article_id {aid!r} in {inc_id}"
        overlap = (set(primary) | set(acceptable)) & set(forbidden)
        assert not overlap, f"{inc_id}: {sorted(overlap)} are both acceptable/primary and forbidden"

    msg = f"Expected at least 1 unanswerable case, got {unanswerable_count}"
    assert unanswerable_count >= 1, msg
    msg = f"Expected at least 5 multi-article incidents, got {multi_article_count}"
    assert multi_article_count >= 5, msg


def test_retired_runbook_is_forbidden_not_acceptable() -> None:
    """The MIR-2026-03 lesson: surfacing the retired v1.0 must FAIL the eval, not pass it.

    Manual §11.5: retired revisions are 'removed before ranking, not ranked low'.
    """
    with COVERAGE_PATH.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        all_rewarded = row["primary_article_ids"] + ";" + row["acceptable_article_ids"]
        assert "KB0010-v1.0" not in all_rewarded, (
            f"{row['incident_id']} rewards the retired revision KB0010-v1.0"
        )

    forbidding = {r["incident_id"] for r in rows if "KB0010-v1.0" in r["forbidden_article_ids"]}
    assert forbidding == {"INC0010052", "INC0009884"}, (
        f"Retired v1.0 must be forbidden exactly on the pool-exhaustion incidents, got {forbidding}"
    )
