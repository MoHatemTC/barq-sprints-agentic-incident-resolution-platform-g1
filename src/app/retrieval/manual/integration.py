"""Bridge the reviewed manifest into the article ingestion path.

Adapted publication units are ordinary ``Article`` records, so they flow
through the existing chunker, embedding engine and ``ingest_articles`` —
no parallel ingestion path. This module only loads the three committed
inputs, runs the adapter (which re-preflights every time), and exposes the
provenance map ``ingest_articles`` needs so ``content_purpose``, ``warning``
and ``source_sections`` survive into chunk payloads.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

from app.models.knowledge import Article
from app.models.knowledge_provenance import KnowledgeProvenance
from app.models.manual_section import ManualSection
from app.retrieval.manual.manifest import load_manifest
from app.retrieval.manual.section_adapter import sections_to_articles


class ManualPublication(NamedTuple):
    """The publication records plus per-article provenance for ingestion."""

    articles: list[Article]
    provenance: dict[str, KnowledgeProvenance]  # keyed by Article.article_id


def load_manual_publication(
    manifest_path: Path,
    sections_path: Path,
    corpus_path: Path,
) -> ManualPublication:
    """Adapt the committed extraction through the committed manifest."""
    manifest = load_manifest(manifest_path)
    raw_sections = json.loads(sections_path.read_text(encoding="utf-8"))
    sections = [ManualSection.model_validate(item) for item in raw_sections["sections"]]
    corpus = [
        Article.model_validate(item) for item in json.loads(corpus_path.read_text(encoding="utf-8"))
    ]

    result = sections_to_articles(sections, manifest, corpus)
    return ManualPublication(
        articles=[record.article for record in result.articles],
        provenance={record.article.article_id: record.provenance for record in result.articles},
    )
