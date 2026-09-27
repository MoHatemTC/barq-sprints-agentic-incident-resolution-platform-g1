"""Bind the manual extraction to the committed PDF edition and the eval labels.

Writes two versioned artifacts:

- ``data/corpus/manual_source.json`` — the PDF identity record (path, sha-256,
  page count) so hash drift is detectable.
- ``data/corpus/manual_coverage.json`` — every eval-referenced evidence label
  as covered (matched to an extracted section number) or explicitly quarantined
  with a recorded blocker. A gap that is not named is a bug in this script.

Run: ``uv run python scripts/manual/validate_manual.py``
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

PDF = Path("data/barq-system-kb.pdf")
SECTIONS = Path("data/corpus/manual_sections.json")
DATASET = Path("data/structured-io/barq_rag_eval_dataset.json")
COVERAGE = Path("data/corpus/manual_coverage.json")
SOURCE = Path("data/corpus/manual_source.json")

#: Labels the extractor does not emit yet. Each entry must carry a real
#: blocker reason — this mapping is the honest record, not an excuse slip.
QUARANTINED_FAMILIES: tuple[tuple[str, str], ...] = (
    (
        "Appendix",
        "appendix sections are consumed for Appendix E relationship parsing "
        "but not emitted as retrieval sections; emitting them is a "
        "follow-up parser extension",
    ),
    (
        "Document control",
        "front-matter blocks are not detected by the section detector; explicit "
        "front-matter records are a follow-up parser extension",
    ),
    (
        "header_footer_noise",
        "running headers/footers are stripped as noise by design; the eval label "
        "describes a must-NOT-retrieve marker, so this is recorded, not a gap",
    ),
)

_GENERIC_BLOCKER = "label not matched to any extracted section; needs review"


class DuplicateSectionError(ValueError):
    """Raised when the extraction contains duplicate section ids."""


def validate_no_duplicate_sections(sections: list[dict]) -> list[str]:
    ids = [s["section_id"] for s in sections]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise DuplicateSectionError(f"duplicate section ids: {duplicates}")
    return ids


def eval_labels(dataset: dict) -> set[str]:
    labels: set[str] = set()
    for session in dataset["sessions"]:
        for turn in session["turns"]:
            for label in turn.get("expected_sections", []) + turn.get("must_not_retrieve", []):
                if label and label != "—":
                    labels.add(label)
    return labels


def turns_affected_by(dataset: dict, labels: set[str]) -> int:
    affected = 0
    for session in dataset["sessions"]:
        for turn in session["turns"]:
            turn_labels = set(turn.get("expected_sections", [])) | set(
                turn.get("must_not_retrieve", [])
            )
            if turn_labels & labels:
                affected += 1
    return affected


def _blocker_for(label: str) -> str:
    for family, reason in QUARANTINED_FAMILIES:
        if label == family or label.startswith(family):
            return reason
    return _GENERIC_BLOCKER


def build_coverage() -> dict:
    raw = json.loads(SECTIONS.read_text(encoding="utf-8"))
    sections = raw["sections"] if isinstance(raw, dict) else raw
    validate_no_duplicate_sections(sections)
    numbers = {s["section_number"] for s in sections}

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    covered: list[str] = []
    quarantined: list[dict[str, str]] = []
    for label in sorted(eval_labels(dataset)):
        key = label.split(" ")[0]
        if re.match(r"^\d", key) and key in numbers:
            covered.append(label)
            continue
        quarantined.append({"label": label, "blocker": _blocker_for(label)})

    quarantined_labels = {q["label"] for q in quarantined}
    return {
        "sections_extracted": len(sections),
        "covered_labels": len(covered),
        "covered_section_numbers": sorted(numbers),
        "covered": covered,
        "quarantined": quarantined,
        "quarantined_turns_affected": turns_affected_by(dataset, quarantined_labels),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate only; fail on drift")
    args = parser.parse_args()

    if not PDF.exists():
        print(f"Error: source PDF not found at {PDF}", file=sys.stderr)
        return 1
    if not SECTIONS.exists():
        print(
            f"Error: extraction not found at {SECTIONS}; run extract_manual.py first",
            file=sys.stderr,
        )
        return 1

    pdf_sha = hashlib.sha256(PDF.read_bytes()).hexdigest()
    source = {
        "pdf_path": str(PDF),
        "pdf_sha256": pdf_sha,
        "extractor": "scripts/manual/extract_manual.py",
    }
    coverage = build_coverage()
    coverage["source_pdf_sha256"] = pdf_sha

    if args.check and SOURCE.exists() and COVERAGE.exists():
        old_source = json.loads(SOURCE.read_text(encoding="utf-8"))
        old_coverage = json.loads(COVERAGE.read_text(encoding="utf-8"))
        if old_source["pdf_sha256"] != pdf_sha:
            print("Error: PDF hash drifted from the recorded source; re-extract", file=sys.stderr)
            return 1
        old_quarantined = {q["label"] for q in old_coverage["quarantined"]}
        new_quarantined = {q["label"] for q in coverage["quarantined"]}
        if old_quarantined - new_quarantined:
            print("Error: previously quarantined labels silently vanished", file=sys.stderr)
            return 1

    SOURCE.write_text(json.dumps(source, indent=2) + "\n")
    COVERAGE.write_text(json.dumps(coverage, indent=2) + "\n")
    print(
        f"coverage: {coverage['covered_labels']} labels covered, "
        f"{len(coverage['quarantined'])} quarantined "
        f"({coverage['quarantined_turns_affected']} turns affected), "
        f"{coverage['sections_extracted']} sections"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
