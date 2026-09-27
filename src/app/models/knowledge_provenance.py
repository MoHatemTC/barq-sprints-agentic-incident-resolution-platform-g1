"""Typed provenance for knowledge adapted from external sources.

``Article`` is a strict, extra-forbid contract shared by every pipeline stage,
so it cannot carry source-traceability fields. Provenance travels beside it:
``KnowledgeProvenance`` records where an article's content came from and how
it may be used, and the adapter pairs the two into one publication record.

Ingestion decides which provenance fields must survive into chunk payloads —
metadata needed for retrieval decisions may live only here transiently, never
only in the manifest file.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ContentPurpose(StrEnum):
    """What a publication unit's content is for; drives actionability downstream.

    ``current_procedure`` may surface as executable repair steps. ``reference``
    may guide and constrain but must not be turned into a fix. ``historical``
    and ``archival`` narrate the past; ``warning`` explains a retirement.
    Chunking attaches ``warning`` text to historical/warning content so the
    caveat can never be detached from the text it governs.
    """

    CURRENT_PROCEDURE = "current_procedure"
    REFERENCE = "reference"
    HISTORICAL = "historical"
    ARCHIVAL = "archival"
    WARNING = "warning"


class KnowledgeProvenance(BaseModel):
    """Source traceability for one adapted publication unit.

    Two distinct content hashes exist by design: the manifest pins the
    *source section* text (extraction-side drift detection), while
    ``content_sha256`` here pins the *article body* that will actually be
    ingested (ingestion-side verification).
    """

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(..., description="Manifest unit this record was adapted from")
    source_document_id: str = Field(
        ..., description='Source document edition, e.g. "barq-manual-v4.0"'
    )
    source_pdf_sha256: str = Field(..., description="SHA-256 of the source PDF edition")
    source_sections: tuple[str, ...] = Field(
        default=(),
        description='Source section numbers, e.g. ("6.4",); empty for platform-authored units',
    )
    source_section_ids: tuple[str, ...] = Field(
        default=(), description='Extraction ids, e.g. ("section-6.4",)'
    )
    pages: tuple[int, ...] = Field(default=(), description="Union of source page spans")
    content_sha256: str = Field(..., description="SHA-256 of the article body being ingested")
    content_purpose: ContentPurpose
    extraction_method: str | None = Field(
        default=None, description="How the source text was produced: prose/table/ocr/layout"
    )
    ocr_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reliability_note: str | None = Field(
        default=None, description="Extraction-quality caveat carried from the section"
    )
    derives_from_articles: tuple[str, ...] = Field(
        default=(),
        description="KB numbers (or versioned unique keys) whose content this unit reproduces",
    )
    supersedes: tuple[str, ...] = Field(
        default=(),
        description="Article identities this unit replaces; filled only after verified inventory",
    )
    warning: str | None = Field(
        default=None,
        description="Caveat attached to this content during chunking; never detached from it",
    )
