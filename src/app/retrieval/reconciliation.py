"""Dry-run reconciliation planning for a corpus release.

Answers one question read-only: *what would a release have to remove?* —
raw manual-section points that cannot satisfy the mandatory filters, and
article points superseded by manifest units. It removes nothing: execution
is an explicitly authorized, inventory-bound release step, never a side
effect of importing or seeding. Human-captured and baseline records are
never candidates by construction.
"""

from __future__ import annotations

from dataclasses import dataclass

from qdrant_client import QdrantClient

from app.retrieval.manual.manifest import ManualKBManifest

RAW_SECTION_DOC_TYPE = "manual_section"


@dataclass(frozen=True)
class ReconciliationPlan:
    """What a release would remove; empty means a documented no-op."""

    raw_section_point_ids: tuple[str, ...]
    superseded_article_ids: tuple[str, ...]
    notes: tuple[str, ...]


def build_reconciliation_plan(
    client: QdrantClient,
    collection_name: str,
    manifest: ManualKBManifest,
) -> ReconciliationPlan:
    """List removal candidates from the live collection; write nothing."""
    superseded_numbers = {
        ref.split("-v", 1)[0] for unit in manifest.units for ref in unit.supersedes
    }

    raw_section_point_ids: list[str] = []
    superseded_article_ids: list[str] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection_name,
            with_payload=True,
            limit=256,
            offset=offset,
        )
        for point in points:
            payload = point.payload or {}
            if payload.get("doc_type") == RAW_SECTION_DOC_TYPE:
                raw_section_point_ids.append(str(point.id))
            elif payload.get("article_number") in superseded_numbers:
                superseded_article_ids.append(str(payload.get("article_id") or point.id))
        if offset is None:
            break

    notes = []
    if superseded_numbers:
        notes.append(
            f"manifest supersedes {len(superseded_numbers)} article identities; "
            "verify each replacement is live and evaluated before removal"
        )
    else:
        notes.append(
            "manifest declares no supersessions; stressor retirement stays a "
            "documented no-op until the verified inventory (plan step 6a)"
        )
    if raw_section_point_ids:
        notes.append(
            f"{len(raw_section_point_ids)} raw manual-section points found; they can "
            "never satisfy the mandatory filters and are removed only through the "
            "explicit release plan"
        )
    else:
        notes.append("no raw manual-section points found in this collection")

    return ReconciliationPlan(
        raw_section_point_ids=tuple(sorted(raw_section_point_ids)),
        superseded_article_ids=tuple(sorted(set(superseded_article_ids))),
        notes=tuple(notes),
    )
