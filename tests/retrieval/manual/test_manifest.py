"""Manifest contract: coverage, identity stability, lifecycle and security gates.

The manifest is the append-only mapping from extracted manual sections to
publication units. These tests pin the contracts the rest of the integration
depends on: every section is covered exactly once, alias units mirror corpus
metadata instead of copying it, restricted content cannot be downgraded,
retired instructions can only live in historical/warning units with a warning
attached, KB2xxx numbers are allocated once and never renumbered, and content
drift forces an explicit manifest update rather than a silent one.
"""

import json
from pathlib import Path

import pytest

from app.models.knowledge import Article, SecurityLevel, WorkflowState
from app.models.manual_section import ManualSection, ManualSectionType
from app.retrieval.manual.manifest import (
    ManifestPreflightError,
    assign_article_numbers,
    load_manifest,
    parse_manifest,
    validate_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / "data" / "corpus" / "manual_kb_manifest.json"
SECTIONS = REPO_ROOT / "data" / "corpus" / "manual_sections.json"
CORPUS = REPO_ROOT / "data" / "corpus" / "barq_articles.json"
PDF = REPO_ROOT / "data" / "barq-system-kb.pdf"


def _real_sections() -> list[ManualSection]:
    raw = json.loads(SECTIONS.read_text())
    return [ManualSection.model_validate(item) for item in raw["sections"]]


def _real_corpus() -> list[Article]:
    raw = json.loads(CORPUS.read_text())
    return [Article.model_validate(item) for item in raw]


def _section(number: str, body: str = "Body text long enough to ingest.") -> ManualSection:
    return ManualSection(
        section_id=f"section-{number}",
        section_number=number,
        title=f"Section {number} title",
        body=body,
        content_type=ManualSectionType.PROSE,
        pages=(1,),
    )


def _article(
    number: str,
    version: str = "1.0",
    security: SecurityLevel = SecurityLevel.INTERNAL,
    state: WorkflowState = WorkflowState.PUBLISHED,
) -> Article:
    return Article(
        article_number=number,
        version=version,
        title=f"Article {number} title",
        body=f"Body of {number} at {version}.",
        short_description=f"Short description of {number}.",
        category="reference",
        service="knowledge-base",
        workflow_state=state,
        security_level=security,
    )


def _alias_unit(number: str, section: str, version: str = "1.0", **over) -> dict:
    unit = {
        "unit_id": f"section-{section}",
        "source_sections": [section],
        "kind": "alias",
        "article_number": number,
        "version": version,
        "content_purpose": "current_procedure",
    }
    unit.update(over)
    return unit


def _new_unit(section: str, number: str | None, **over) -> dict:
    unit = {
        "unit_id": f"section-{section}",
        "source_sections": [section],
        "kind": "new",
        "article_number": number,
        "version": "1.0" if number else None,
        "content_purpose": "reference",
        "workflow_state": "published",
        "security_level": "internal",
        "category": "reference",
        "service": "knowledge-base",
    }
    unit.update(over)
    return unit


def _raw_manifest(units: list[dict], **over) -> dict:
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
            "rule": "append-only allocation, never derived from list position or content hash",
        },
        "vocabulary": {
            "categories": {"reference": "Reference material"},
            "services": {"knowledge-base": "Knowledge base"},
        },
        "units": units,
    }
    raw.update(over)
    return raw


def _section_hash(*sections: ManualSection) -> str:
    import hashlib

    joined = "\n\n".join(s.body for s in sections)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _with_hash(unit: dict, *sections: ManualSection) -> dict:
    unit["content_sha256"] = _section_hash(*sections)
    return unit


# --- the committed artifact -------------------------------------------------


def test_committed_manifest_covers_every_extracted_section_exactly_once() -> None:
    manifest = load_manifest(MANIFEST)
    sections = _real_sections()
    validate_manifest(manifest, sections, _real_corpus())

    covered = [n for unit in manifest.units for n in unit.source_sections]
    extracted = [s.section_number for s in sections]
    assert sorted(covered) == sorted(extracted), (
        "manifest and extraction disagree; a section must be covered exactly once"
    )


def test_committed_manifest_preflight_passes_against_current_inputs() -> None:
    manifest = load_manifest(MANIFEST)
    validate_manifest(manifest, _real_sections(), _real_corpus())


# --- alias rules (Section 6 locking) ----------------------------------------


def test_alias_target_must_exist_published_at_pinned_version() -> None:
    sections = [_section("6.4")]
    corpus = [_article("KB0001", "2.0")]

    missing = parse_manifest(
        _raw_manifest([_with_hash(_alias_unit("KB0009", "6.4", "2.0"), sections[0])])
    )
    with pytest.raises(ManifestPreflightError, match="KB0009"):
        validate_manifest(missing, sections, corpus)

    retired = parse_manifest(
        _raw_manifest(
            [
                _with_hash(
                    _alias_unit("KB0010", "6.4", "1.0"),
                    sections[0],
                )
            ]
        )
    )
    with pytest.raises(ManifestPreflightError, match="retired"):
        validate_manifest(
            retired, sections, [_article("KB0010", "1.0", state=WorkflowState.RETIRED)]
        )


def test_alias_units_may_not_copy_corpus_metadata() -> None:
    sections = [_section("6.4")]
    unit = _with_hash(
        _alias_unit("KB0001", "6.4", "2.0", security_level="internal", category="network"),
        sections[0],
    )
    manifest = parse_manifest(_raw_manifest([unit]))
    with pytest.raises(ManifestPreflightError, match="mirror"):
        validate_manifest(manifest, sections, [_article("KB0001", "2.0")])


# --- lifecycle and security precedence ---------------------------------------


def test_restricted_derivation_cannot_be_downgraded() -> None:
    section = _section("7.3")
    unit = _with_hash(
        _new_unit("7.3", "KB2001", security_level="internal", derives_from_articles=["KB0004"]),
        section,
    )
    manifest = parse_manifest(_raw_manifest([unit]))
    corpus = [_article("KB0004", "1.0", security=SecurityLevel.RESTRICTED)]
    with pytest.raises(ManifestPreflightError, match="restricted"):
        validate_manifest(manifest, [section], corpus)


def test_retired_story_needs_historical_or_warning_purpose_and_a_warning() -> None:
    mir_narrative = _section("9.2")
    worked_record = _section("7.5")
    sections = [mir_narrative, worked_record]
    corpus = [
        _article("KB0010", "1.0", state=WorkflowState.RETIRED),
        _article("KB0010", "2.0"),
    ]
    # The 7.5 record pins the current published version, so it never trips
    # the retired rules — every manifest below carries it alongside 9.2.
    pinned = _with_hash(
        _new_unit("7.5", "KB2002", derives_from_articles=["KB0010-v2.0"]), worked_record
    )

    active = parse_manifest(
        _raw_manifest(
            [
                _with_hash(
                    _new_unit(
                        "9.2",
                        "KB2001",
                        content_purpose="current_procedure",
                        derives_from_articles=["KB0010"],
                    ),
                    mir_narrative,
                ),
                pinned,
            ]
        )
    )
    with pytest.raises(ManifestPreflightError, match="retired"):
        validate_manifest(active, sections, corpus)

    silent_history = parse_manifest(
        _raw_manifest(
            [
                _with_hash(
                    _new_unit(
                        "9.2",
                        "KB2001",
                        content_purpose="historical",
                        derives_from_articles=["KB0010"],
                    ),
                    mir_narrative,
                ),
                pinned,
            ]
        )
    )
    with pytest.raises(ManifestPreflightError, match="warning"):
        validate_manifest(silent_history, sections, corpus)

    told = parse_manifest(
        _raw_manifest(
            [
                _with_hash(
                    _new_unit(
                        "9.2",
                        "KB2001",
                        content_purpose="historical",
                        derives_from_articles=["KB0010"],
                        warning="This narrative repeats a retired instruction; follow KB0010 v2.0.",
                    ),
                    mir_narrative,
                ),
                pinned,
            ]
        )
    )
    validate_manifest(told, sections, corpus)


# --- content drift ------------------------------------------------------------


def test_content_drift_fails_preflight_naming_the_unit() -> None:
    section = _section("6.2")
    stale = _with_hash(_new_unit("6.2", "KB2001"), _section("6.2", "The old extraction text."))
    manifest = parse_manifest(_raw_manifest([stale]))
    with pytest.raises(ManifestPreflightError, match="section-6.2"):
        validate_manifest(manifest, [section], [])


# --- vocabulary ----------------------------------------------------------------


def test_category_and_service_must_be_declared_slug_vocabulary() -> None:
    section = _section("6.2")
    undeclared = _with_hash(_new_unit("6.2", "KB2001", category="Knowledge Base"), section)
    manifest = parse_manifest(_raw_manifest([undeclared]))
    with pytest.raises(ManifestPreflightError, match="vocabulary|slug"):
        validate_manifest(manifest, [section], [])


# --- identity allocation --------------------------------------------------------


def test_new_numbers_stay_in_range_and_kb2000_is_never_allocated() -> None:
    section = _section("1.1")
    out_of_range = parse_manifest(_raw_manifest([_with_hash(_new_unit("1.1", "KB3000"), section)]))
    with pytest.raises(ManifestPreflightError, match="range"):
        validate_manifest(out_of_range, [section], [])

    reserved = parse_manifest(_raw_manifest([_with_hash(_new_unit("1.1", "KB2000"), section)]))
    with pytest.raises(ManifestPreflightError, match="KB2000"):
        validate_manifest(reserved, [section], [])


def test_new_unit_cannot_collide_with_an_existing_corpus_identity() -> None:
    section = _section("1.1")
    manifest = parse_manifest(_raw_manifest([_with_hash(_new_unit("1.1", "KB2001"), section)]))
    with pytest.raises(ManifestPreflightError, match="collision"):
        validate_manifest(manifest, [section], [_article("KB2001", "1.0")])


def test_deallocated_numbers_are_never_reissued() -> None:
    first, second, added = _section("3.1"), _section("3.2"), _section("2.9")
    raw = _raw_manifest(
        [
            _with_hash(_new_unit("3.1", "KB2001"), first),
            _with_hash(_new_unit("3.2", "KB2002"), second),
            _with_hash(_new_unit("2.9", None), added),
        ]
    )
    raw["identity_policy"]["deallocated"] = ["KB2003"]
    allocated = assign_article_numbers(parse_manifest(raw))
    numbers = {u.unit_id: u.article_number for u in allocated.units}
    assert numbers["section-2.9"] == "KB2004", "a burned number must not come back"

    reclaim_raw = _raw_manifest([_with_hash(_new_unit("2.9", "KB2003"), added)])
    reclaim_raw["identity_policy"]["deallocated"] = ["KB2003"]
    with pytest.raises(ManifestPreflightError, match="deallocated"):
        validate_manifest(parse_manifest(reclaim_raw), [added], [])


def test_allocation_is_append_only_and_idempotent() -> None:
    first, second, added = _section("3.1"), _section("3.2"), _section("2.9")
    units = [
        _with_hash(_new_unit("3.1", "KB2001"), first),
        _with_hash(_new_unit("3.2", "KB2002"), second),
        # A late addition, listed before already-allocated units.
        _with_hash(_new_unit("2.9", None), added),
    ]
    manifest = parse_manifest(_raw_manifest(units))

    allocated = assign_article_numbers(manifest)
    numbers = {u.unit_id: u.article_number for u in allocated.units}
    assert numbers["section-2.9"] == "KB2003"
    assert numbers["section-3.1"] == "KB2001"
    assert numbers["section-3.2"] == "KB2002"

    reallocated = assign_article_numbers(allocated)
    assert [u.article_number for u in reallocated.units] == [
        u.article_number for u in allocated.units
    ]


def test_allocation_fails_when_the_range_is_exhausted() -> None:
    section = _section("1.1")
    units = [_with_hash(_new_unit("1.1", f"KB{n}"), section) for n in range(2001, 3000)]
    units.append(_with_hash(_new_unit("9.9", None), section))
    manifest = parse_manifest(_raw_manifest(units))
    with pytest.raises(ManifestPreflightError, match="exhaust"):
        assign_article_numbers(manifest)


# --- PDF edition pin ------------------------------------------------------------


def test_manifest_pins_the_committed_pdf_edition() -> None:
    import hashlib

    manifest = load_manifest(MANIFEST)
    assert manifest.source.pdf_sha256 == hashlib.sha256(PDF.read_bytes()).hexdigest()
