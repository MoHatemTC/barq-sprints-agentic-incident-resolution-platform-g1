"""Reconciliation planning: read-only identification of removal candidates.

The plan lists exactly the raw manual-section points (which can never satisfy
the mandatory filters) and article points the manifest supersedes — nothing
else, and nothing is deleted here. Human-captured and baseline records must
never appear as candidates.
"""

from pathlib import Path
from unittest.mock import MagicMock

from app.retrieval.manual.manifest import load_manifest
from app.retrieval.reconciliation import build_reconciliation_plan

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "data" / "corpus" / "manual_kb_manifest.json"


class _Point:
    def __init__(self, point_id: str, payload: dict) -> None:
        self.id = point_id
        self.payload = payload


def _client_with(points: list[_Point]) -> MagicMock:
    client = MagicMock()
    client.scroll.return_value = (points, None)
    return client


def test_plan_lists_only_raw_sections_and_superseded_articles() -> None:
    points = [
        _Point("raw-1", {"doc_type": "manual_section", "section_id": "section-3.4"}),
        _Point("raw-2", {"doc_type": "manual_section"}),
        _Point("art-1", {"article_number": "KB0005", "article_id": "KB0005-v4.0"}),
        _Point("human-1", {"article_number": "KB1002", "workflow_state": "human_resolved"}),
        _Point("noise-1", {}),
    ]
    plan = build_reconciliation_plan(_client_with(points), "col", load_manifest(MANIFEST))

    assert plan.raw_section_point_ids == ("raw-1", "raw-2")
    assert plan.superseded_article_ids == ()
    assert any("no-op" in note for note in plan.notes)
    assert any("2 raw manual-section points" in note for note in plan.notes)


def test_superseded_articles_are_matched_by_number() -> None:
    points = [
        _Point("art-1", {"article_number": "KB0005", "article_id": "KB0005-v4.0"}),
        _Point("human-1", {"article_number": "KB1002", "workflow_state": "human_resolved"}),
    ]
    # Simulate the reviewed supersession step 6a will add, without touching the
    # committed draft: parse the same manifest and inject one supersession.
    from app.retrieval.manual.manifest import parse_manifest

    raw = {
        "manifest_version": "test",
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
            "categories": {"reference": "Reference"},
            "services": {"knowledge-base": "KB"},
        },
        "units": [
            {
                "unit_id": "section-1.1",
                "source_sections": ["1.1"],
                "kind": "new",
                "article_number": "KB2001",
                "version": "1.0",
                "content_purpose": "reference",
                "workflow_state": "published",
                "security_level": "internal",
                "category": "reference",
                "service": "knowledge-base",
                "supersedes": ["KB0005-v1.0"],
            }
        ],
    }
    plan = build_reconciliation_plan(_client_with(points), "col", parse_manifest(raw))

    assert plan.superseded_article_ids == ("KB0005-v4.0",)
    assert plan.raw_section_point_ids == ()
    assert any("supersedes 1" in note for note in plan.notes)
