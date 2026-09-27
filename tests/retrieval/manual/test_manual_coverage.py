"""Extraction coverage contract: what the evaluation needs, the extraction must have.

The coverage report binds the committed extraction to the committed PDF edition
(sha-256) and to the eval's evidence labels. Every eval-referenced label is
either covered by a section or explicitly quarantined with a recorded blocker —
silent gaps are the failure mode this contract exists to prevent.
"""

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PDF = REPO_ROOT / "data" / "barq-system-kb.pdf"
SECTIONS = REPO_ROOT / "data" / "corpus" / "manual_sections.json"
COVERAGE = REPO_ROOT / "data" / "corpus" / "manual_coverage.json"
SOURCE = REPO_ROOT / "data" / "corpus" / "manual_source.json"
DATASET = REPO_ROOT / "data" / "structured-io" / "barq_rag_eval_dataset.json"
VALIDATOR = REPO_ROOT / "scripts" / "manual" / "validate_manual.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_manual_under_test", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _eval_labels() -> set[str]:
    data = json.loads(DATASET.read_text())
    labels: set[str] = set()
    for session in data["sessions"]:
        for turn in session["turns"]:
            for label in turn.get("expected_sections", []) + turn.get("must_not_retrieve", []):
                if label and label != "—":
                    labels.add(label)
    return labels


def test_pdf_source_hash_is_recorded_and_current() -> None:
    source = json.loads(SOURCE.read_text())
    assert source["pdf_path"] == "data/barq-system-kb.pdf"
    assert source["pdf_sha256"] == pytest.importorskip("hashlib").sha256(PDF.read_bytes()).hexdigest()


def test_every_numbered_eval_label_is_covered() -> None:
    import re

    coverage = json.loads(COVERAGE.read_text())
    covered = set(coverage["covered_section_numbers"])
    numbered = {
        label
        for label in _eval_labels()
        if re.match(r"^\d+(\.\d+)?", label)
    }
    missing = {label for label in numbered if label.split(" ")[0] not in covered}
    assert not missing, f"eval-referenced sections missing from extraction: {sorted(missing)}"


def test_gaps_are_quarantined_with_blockers_not_silent() -> None:
    coverage = json.loads(COVERAGE.read_text())
    quarantined = {q["label"]: q["blocker"] for q in coverage["quarantined"]}
    for label in ("Appendix B.1", "Document control"):
        assert label in quarantined, f"{label} is neither covered nor quarantined"
        assert quarantined[label].strip()
    turns_affected = coverage["quarantined_turns_affected"]
    assert turns_affected >= 0 and isinstance(turns_affected, int)


def test_no_duplicate_section_ids(tmp_path: Path) -> None:
    module = _load_validator()
    sections = json.loads(SECTIONS.read_text())
    sections = sections["sections"] if isinstance(sections, dict) else sections
    duplicated = sections + [dict(sections[0])]
    with pytest.raises(module.DuplicateSectionError):
        module.validate_no_duplicate_sections(duplicated)
    module.validate_no_duplicate_sections(sections)


def test_ocr_confidence_survives_chunk_serialization() -> None:
    from app.models.manual_section import ManualSectionChunk, ManualSectionPayload, ManualSectionType

    chunk = ManualSectionChunk(
        chunk_id="section-2.1#c0",
        section_id="section-2.1",
        chunk_index=0,
        total_chunks=1,
        text="Extracted table body",
        section_number="2.1",
        section_title="Priority matrix",
        pages=(9, 10),
        content_type=ManualSectionType.TABLE,
        ocr_confidence=0.42,
    )
    payload = ManualSectionPayload.from_chunk(chunk)
    assert payload.ocr_confidence == 0.42, "reliability metadata was dropped"
