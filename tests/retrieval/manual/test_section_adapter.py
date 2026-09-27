"""Section→Article adapter contract: aliases reuse, new units stay source-faithful.

The adapter turns validated manifest units plus extracted sections into the
Article-shaped records the existing pipeline ingests. The contracts pinned
here: Section 6 aliases resolve to the existing corpus articles without
allocation or metadata drift, the article index creates nothing, the archived
scan never becomes current-procedure evidence, new articles carry the section
body verbatim with manifest metadata (no fabricated filler), historical
narrative inherits restricted and carries its warning, unusable content is
rejected explicitly, and the whole output is deterministic.
"""

import hashlib
import json
from pathlib import Path

import pytest

from app.models.knowledge import Article, SecurityLevel, WorkflowState
from app.models.manual_section import ManualSection, ManualSectionType
from app.retrieval.manual.manifest import load_manifest, parse_manifest, validate_manifest
from app.retrieval.manual.section_adapter import SectionAdapterError, sections_to_articles

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / "data" / "corpus" / "manual_kb_manifest.json"
SECTIONS = REPO_ROOT / "data" / "corpus" / "manual_sections.json"
CORPUS = REPO_ROOT / "data" / "corpus" / "barq_articles.json"


def _real_inputs() -> tuple[list[ManualSection], object, list[Article]]:
    raw = json.loads(SECTIONS.read_text())
    sections = [ManualSection.model_validate(item) for item in raw["sections"]]
    manifest = load_manifest(MANIFEST)
    corpus = [Article.model_validate(item) for item in json.loads(CORPUS.read_text())]
    validate_manifest(manifest, sections, corpus)
    return sections, manifest, corpus


def _by_article_number(result) -> dict:
    return {a.article.article_number: a for a in result.articles}


# --- Section 6 aliases -------------------------------------------------------


def test_section6_aliases_reuse_corpus_articles_unchanged_and_idempotently() -> None:
    sections, manifest, corpus = _real_inputs()
    result = sections_to_articles(sections, manifest, corpus)

    adapted = _by_article_number(result)["KB0001"]
    corpus_article = next(a for a in corpus if a.article_number == "KB0001")
    assert adapted.article == corpus_article, "alias must mirror the corpus article, not rebuild it"

    rerun = sections_to_articles(sections, manifest, corpus)
    assert [a.model_dump() for a in rerun.articles] == [a.model_dump() for a in result.articles]


def test_article_index_maps_to_existing_articles_without_creating_one() -> None:
    sections, manifest, corpus = _real_inputs()
    result = sections_to_articles(sections, manifest, corpus)

    index_unit = next(u for u in manifest.units if u.source_sections == ("6.1",))
    assert index_unit.kind.value == "index_alias"
    assert index_unit.article_number is None, "6.1 must never allocate a KB2xxx number"
    assert result.index_aliases["6.1"] == [f"KB{n:04d}" for n in range(1, 11)]
    assert not [a for a in result.articles if a.provenance.source_sections == ("6.1",)], (
        "the index aliases existing articles; it must not produce one"
    )


# --- special Section 6 decisions ---------------------------------------------


def test_archived_scan_is_not_current_procedure_evidence() -> None:
    sections, manifest, corpus = _real_inputs()
    result = sections_to_articles(sections, manifest, corpus)

    kb0005 = _by_article_number(result)["KB0005"]
    archival = [a for a in result.articles if "6.3" in a.provenance.source_sections]
    assert len(archival) == 1
    unit = archival[0]
    assert unit.article.article_number != "KB0005", (
        "the scan never substitutes for the live article"
    )
    assert unit.article.workflow_state != WorkflowState.PUBLISHED, (
        "the archived scan must never pass the mandatory lifecycle filter as current evidence"
    )
    assert (unit.article.category, unit.article.service) != (
        kb0005.article.category,
        kb0005.article.service,
    ), "6.3 must not mirror KB0005's corpus metadata"


def test_symptom_finder_becomes_one_new_reference_article() -> None:
    sections, manifest, corpus = _real_inputs()
    result = sections_to_articles(sections, manifest, corpus)

    section_62 = next(s for s in sections if s.section_number == "6.2")
    adapted = [a for a in result.articles if a.provenance.source_sections == ("6.2",)]
    assert len(adapted) == 1
    unit = adapted[0]
    assert unit.article.article_number.startswith("KB2")
    assert unit.article.workflow_state == WorkflowState.PUBLISHED
    assert unit.article.security_level == SecurityLevel.INTERNAL
    assert unit.article.body == section_62.body, "body must be the section text verbatim"
    assert unit.provenance.content_purpose.value == "reference"


# --- lifecycle / security in adapted output ----------------------------------


def test_historical_units_inherit_restricted_and_carry_warning() -> None:
    sections, manifest, corpus = _real_inputs()
    result = sections_to_articles(sections, manifest, corpus)

    historical = [a for a in result.articles if "9.2" in a.provenance.source_sections]
    assert len(historical) == 1
    unit = historical[0]
    assert unit.provenance.content_purpose.value == "historical"
    assert unit.article.security_level == SecurityLevel.RESTRICTED
    assert unit.provenance.warning, "the retired-instruction narrative must carry a warning"


def test_provenance_traces_source_and_content() -> None:
    sections, manifest, corpus = _real_inputs()
    result = sections_to_articles(sections, manifest, corpus)

    unit = next(a for a in result.articles if a.provenance.source_sections == ("6.2",))
    expected_body_hash = hashlib.sha256(unit.article.body.encode("utf-8")).hexdigest()
    assert unit.provenance.content_sha256 == expected_body_hash
    assert unit.provenance.source_section_ids == ("section-6.2",)
    assert unit.provenance.source_pdf_sha256 == load_manifest(MANIFEST).source.pdf_sha256
    assert unit.provenance.unit_id == "section-6.2"


# --- Article contract honesty -------------------------------------------------


def _single_new_unit_inputs(section: ManualSection, **over) -> tuple:
    unit = {
        "unit_id": f"section-{section.section_number}",
        "source_sections": [section.section_number],
        "kind": "new",
        "article_number": "KB2001",
        "version": "1.0",
        "content_purpose": "reference",
        "workflow_state": "published",
        "security_level": "internal",
        "category": "reference",
        "service": "knowledge-base",
        "content_sha256": hashlib.sha256(section.body.encode("utf-8")).hexdigest(),
    }
    unit.update(over)
    raw = {
        "manifest_version": "1.0",
        "source": {
            "document_id": "barq-manual-v4.0",
            "pdf_path": "data/barq-system-kb.pdf",
            "pdf_sha256": "0" * 64,
            "sections_artifact": "data/corpus/manual_sections.json",
        },
        "identity_policy": {
            "new_range_min": 2001,
            "new_range_max": 2999,
            "never_allocated": ["KB2000"],
            "rule": "append-only",
        },
        "vocabulary": {
            "categories": {"reference": "Reference material"},
            "services": {"knowledge-base": "Knowledge base"},
        },
        "units": [unit],
    }
    manifest = parse_manifest(raw)
    validate_manifest(manifest, [section], [])
    return [section], manifest, []


def test_unusable_content_is_rejected_not_fabricated() -> None:
    tiny = ManualSection(
        section_id="section-9.9",
        section_number="9.9",
        title="Tiny",
        body="abc",
        content_type=ManualSectionType.PROSE,
        pages=(1,),
    )
    with pytest.raises(SectionAdapterError, match="9.9"):
        sections_to_articles(*_single_new_unit_inputs(tiny))

    long_title = ManualSection(
        section_id="section-9.8",
        section_number="9.8",
        title="X" * 250,
        body="A body long enough to satisfy the article contract.",
        content_type=ManualSectionType.PROSE,
        pages=(1,),
    )
    with pytest.raises(SectionAdapterError, match="title"):
        sections_to_articles(*_single_new_unit_inputs(long_title))

    rescued = ManualSection(
        section_id="section-9.8",
        section_number="9.8",
        title="X" * 250,
        body="A body long enough to satisfy the article contract.",
        content_type=ManualSectionType.PROSE,
        pages=(1,),
    )
    sections, manifest, corpus = _single_new_unit_inputs(rescued, title="Rescued short title")
    result = sections_to_articles(sections, manifest, corpus)
    assert result.articles[0].article.title == "Rescued short title"


def test_adapter_output_is_deterministic_and_sorted() -> None:
    sections, manifest, corpus = _real_inputs()
    first = sections_to_articles(sections, manifest, corpus)
    second = sections_to_articles(sections, manifest, corpus)

    numbers = [(a.article.article_number, a.article.version) for a in first.articles]
    assert numbers == sorted(numbers)
    assert first == second
