"""Section→Article adapter: manifest + extraction + corpus → publication records.

Turns validated manifest units into the ``Article``-shaped records the
existing pipeline ingests, each paired with its ``KnowledgeProvenance``:

- alias units resolve to the corpus article itself — metadata is mirrored,
  never rebuilt, and no new number is allocated;
- index aliases resolve to the articles they list and produce nothing;
- new units build an article from the covered section bodies verbatim (or,
  for platform-authored units, from the manifest body) with the manifest's
  reviewed metadata; content that cannot satisfy the Article contract is
  rejected with a named error — never padded or reworded.

The adapter re-runs manifest preflight on every call, so output is only ever
produced from a manifest that currently agrees with the extraction and corpus.
"""

from __future__ import annotations

import hashlib
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict

from app.models.knowledge import Article
from app.models.knowledge_provenance import KnowledgeProvenance
from app.models.manual_section import ManualSection
from app.retrieval.manual.manifest import (
    ManifestUnit,
    ManualKBManifest,
    UnitKind,
    validate_manifest,
)

TITLE_MIN = 5
TITLE_MAX = 200
SHORT_DESCRIPTION_MAX = 255
BODY_MIN = 10


class SectionAdapterError(ValueError):
    """A unit cannot satisfy the Article contract; the message names the unit."""


class AdaptedArticle(BaseModel):
    """One publication record: the ingested article plus its provenance."""

    model_config = ConfigDict(frozen=True)

    article: Article
    provenance: KnowledgeProvenance


class AdapterResult(NamedTuple):
    articles: list[AdaptedArticle]
    index_aliases: dict[str, list[str]]


def _unit_title(unit: ManifestUnit, sections: list[ManualSection]) -> str:
    title = unit.title or (sections[0].title if sections else None)
    if title is None:
        raise SectionAdapterError(
            f"{unit.unit_id}: platform-authored unit needs an explicit manifest title"
        )
    if not TITLE_MIN <= len(title) <= TITLE_MAX:
        raise SectionAdapterError(
            f"{unit.unit_id}: title length {len(title)} is outside {TITLE_MIN}-{TITLE_MAX}; "
            "set an explicit manifest title instead of truncating"
        )
    return title


def _unit_body(unit: ManifestUnit, sections_by_number: dict[str, ManualSection]) -> str:
    if unit.source_sections:
        body = "\n\n".join(sections_by_number[n].body for n in unit.source_sections)
    else:
        body = unit.body or ""
    if len(body.strip()) < BODY_MIN:
        raise SectionAdapterError(
            f"{unit.unit_id}: body is too short to satisfy the Article contract "
            f"({len(body.strip())} < {BODY_MIN} chars); quarantine it in the manifest "
            "instead of fabricating content"
        )
    return body


def _provenance(
    unit: ManifestUnit,
    article: Article,
    sections: list[ManualSection],
    manifest: ManualKBManifest,
) -> KnowledgeProvenance:
    extraction_method = sections[0].content_type.value if sections else None
    ocr_confidence = next(
        (s.ocr_confidence for s in sections if s.ocr_confidence is not None), None
    )
    reliability_note = next((s.reliability_note for s in sections if s.reliability_note), None)
    return KnowledgeProvenance(
        unit_id=unit.unit_id,
        source_document_id=manifest.source.document_id,
        source_pdf_sha256=manifest.source.pdf_sha256,
        source_sections=tuple(unit.source_sections),
        source_section_ids=tuple(f"section-{n}" for n in unit.source_sections),
        pages=tuple(sorted({page for section in sections for page in section.pages})),
        content_sha256=hashlib.sha256(article.body.encode("utf-8")).hexdigest(),
        content_purpose=unit.content_purpose,
        extraction_method=extraction_method,
        ocr_confidence=ocr_confidence,
        reliability_note=reliability_note,
        derives_from_articles=unit.derives_from_articles,
        supersedes=unit.supersedes,
        warning=unit.warning,
    )


def sections_to_articles(
    sections: list[ManualSection],
    manifest: ManualKBManifest,
    corpus: list[Article],
) -> AdapterResult:
    """Adapt every manifest unit into publication records.

    Deterministic: preflighted input in, articles sorted by identity out —
    two calls over the same inputs produce equal results.
    """
    validate_manifest(manifest, sections, corpus)

    sections_by_number = {section.section_number: section for section in sections}
    corpus_by_key = {article.unique_key: article for article in corpus}

    adapted: list[AdaptedArticle] = []
    index_aliases: dict[str, list[str]] = {}

    for unit in manifest.units:
        unit_sections = [sections_by_number[n] for n in unit.source_sections]

        if unit.kind is UnitKind.INDEX_ALIAS:
            key = unit.source_sections[0] if unit.source_sections else unit.unit_id
            index_aliases[key] = sorted(unit.alias_targets)
            continue

        if unit.kind is UnitKind.ALIAS:
            article = corpus_by_key[unit.unique_key]
        else:
            # Preflight, re-run above, guarantees full metadata on new units.
            assert unit.article_number and unit.version
            assert unit.category and unit.service
            assert unit.workflow_state is not None and unit.security_level is not None
            title = _unit_title(unit, unit_sections)
            body = _unit_body(unit, sections_by_number)
            short_description = unit.short_description or title
            if len(short_description) > SHORT_DESCRIPTION_MAX:
                raise SectionAdapterError(
                    f"{unit.unit_id}: short_description length {len(short_description)} exceeds "
                    f"{SHORT_DESCRIPTION_MAX}; set an explicit manifest short_description"
                )
            article = Article(
                article_number=unit.article_number,
                version=unit.version,
                title=title,
                body=body,
                short_description=short_description,
                category=unit.category,
                service=unit.service,
                workflow_state=unit.workflow_state,
                security_level=unit.security_level,
            )

        adapted.append(
            AdaptedArticle(
                article=article,
                provenance=_provenance(unit, article, unit_sections, manifest),
            )
        )

    adapted.sort(key=lambda record: (record.article.article_number, record.article.version))
    return AdapterResult(articles=adapted, index_aliases=index_aliases)
