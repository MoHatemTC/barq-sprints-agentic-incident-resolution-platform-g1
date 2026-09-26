"""Manual corpus JSON source, the boundary between a pre-extracted manual
corpus file and the chunking/ingestion pipeline, mirroring
`app.retrieval.sources.LocalJSONSource` for KB articles.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from app.models.manual_section import ManualSection
from app.retrieval.extraction.parse_appendix import AppendixERelationships


class ManualSource(Protocol):
    """Anything that can produce validated sections + relationships can feed the pipeline."""

    def load_sections(self) -> tuple[list[ManualSection], AppendixERelationships]: ...


class ManualCorpusJSONSource:
    """Loads a pre-extracted manual corpus JSON file (see module docstring for shape).

    Validation is fail-fast, the same way `LocalJSONSource` treats the
    article corpus: a malformed section in our own extracted corpus must be
    caught here, not several stages further into chunking or ingestion.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def load_sections(self) -> tuple[list[ManualSection], AppendixERelationships]:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "sections" not in raw:
            raise ValueError(f'{self.path} must be a JSON object with a "sections" array')

        sections = [ManualSection.model_validate(item) for item in raw["sections"]]

        rel_raw = raw.get("relationships") or {}
        relationships = AppendixERelationships(
            forward=rel_raw.get("forward", {}),
            reverse=rel_raw.get("reverse", {}),
        )
        return sections, relationships


def sections_and_relationships_to_json(
    sections: list[ManualSection], relationships: AppendixERelationships
) -> dict[str, Any]:
    """Build the JSON-serializable corpus payload.

    Used by both `extract_manual_to_json.py` (to write the corpus file) and
    `test_manual_pipeline.py` (to round-trip-check the schema), so the two
    can never drift apart from each other.
    """
    return {
        "sections": [s.model_dump(mode="json") for s in sections],
        "relationships": {
            "forward": relationships.forward,
            "reverse": relationships.reverse,
        },
    }
