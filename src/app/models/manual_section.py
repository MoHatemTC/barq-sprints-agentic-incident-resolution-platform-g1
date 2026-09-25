from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

SECTION_ID_PREFIX = "section-"


class ManualSectionType(StrEnum):
    """How a section's body text was produced."""

    PROSE = "prose"
    TABLE = "table"
    OCR = "ocr"
    LAYOUT = "layout"


class ManualSection(BaseModel):
    """One structural section of the manual, spanning one or more pages.

    Produced by `section_detector` (plus the content-type-specific renderers
    wired up in `full_document_parser`) before any chunking happens. A
    `ManualSection` is never split across a section boundary downstream —
    `manual_chunking.chunk_section` enforces that invariant.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    section_id: str = Field(..., description='Stable id, e.g. "section-3.4"')
    section_number: str = Field(
        ..., description='Numbered ("3.4", "10.1.2") or lettered appendix ("A", "E.2")'
    )
    title: str = Field(..., min_length=1, max_length=300)
    body: str = Field(
        ...,
        description="Extracted section content, ready for chunking",
    )
    content_type: ManualSectionType
    pages: tuple[int, ...] = Field(..., description="1-indexed page numbers this section spans")
    ocr_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Set only when content_type is OCR; average across the section's pages",
    )
    reliability_note: str | None = Field(
        default=None,
        description="Human-readable caveat surfaced to downstream consumers "
        "(e.g. low OCR confidence) so a low-trust chunk is never silently indistinguishable "
        "from a high-confidence one",
    )

    @field_validator("pages")
    @classmethod
    def validate_pages(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value:
            raise ValueError("pages must not be empty")
        if list(value) != sorted(value) or len(set(value)) != len(value):
            raise ValueError(f"pages must be strictly ascending with no duplicates, got {value!r}")
        return value

    @classmethod
    def build_section_id(cls, section_number: str) -> str:
        """Deterministic section id from a section number, e.g. "3.4" -> "section-3.4"."""
        return f"{SECTION_ID_PREFIX}{section_number}"


class ManualSectionChunk(BaseModel):
    """One retrievable piece of a manual section, produced by the chunker."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    section_id: str = Field(..., description='Parent section id, e.g. "section-3.4"')
    chunk_index: int = Field(default=0, ge=0)
    total_chunks: int = Field(default=1, ge=1)
    text: str = Field(..., min_length=1)

    # Metadata inherited from the parent section.
    section_number: str
    section_title: str
    pages: tuple[int, ...]
    content_type: ManualSectionType

    # Appendix-E-derived relationships.
    related_article_ids: tuple[str, ...] = Field(default_factory=tuple)
    related_incident_ids: tuple[str, ...] = Field(default_factory=tuple)
    related_problem_ids: tuple[str, ...] = Field(default_factory=tuple)
    related_known_error_ids: tuple[str, ...] = Field(default_factory=tuple)
    related_change_ids: tuple[str, ...] = Field(default_factory=tuple)
    related_mir_ids: tuple[str, ...] = Field(default_factory=tuple)

    @classmethod
    def build_chunk_id(cls, section_id: str, chunk_index: int) -> str:
        """Deterministic chunk identifier, e.g. "section-3.4#c0"."""
        return f"{section_id}#c{chunk_index}"

    @classmethod
    def validate_bounds(cls, chunk_index: int, total_chunks: int) -> None:
        if chunk_index >= total_chunks:
            raise ValueError(f"chunk_index {chunk_index} must be < total_chunks {total_chunks}")

    def model_post_init(self, __context: Any) -> None:
        self.validate_bounds(self.chunk_index, self.total_chunks)


class ManualSectionPayload(BaseModel):
    """Schema of a Qdrant point payload stored for each manual section chunk."""

    model_config = ConfigDict(frozen=True)

    doc_type: str = "manual_section"
    document_id: str = "barq-manual-v4.0"

    section_id: str
    section_number: str
    section_title: str
    chunk_index: int
    total_chunks: int
    chunk_text: str
    content_type: ManualSectionType
    pages: list[int]
    ocr_confidence: float | None = None

    related_article_ids: list[str] = Field(default_factory=list)
    related_incident_ids: list[str] = Field(default_factory=list)
    related_problem_ids: list[str] = Field(default_factory=list)
    related_known_error_ids: list[str] = Field(default_factory=list)
    related_change_ids: list[str] = Field(default_factory=list)
    related_mir_ids: list[str] = Field(default_factory=list)

    @classmethod
    def from_chunk(cls, chunk: ManualSectionChunk) -> ManualSectionPayload:
        """Build the payload for one manual section chunk."""
        return cls(
            section_id=chunk.section_id,
            section_number=chunk.section_number,
            section_title=chunk.section_title,
            chunk_index=chunk.chunk_index,
            total_chunks=chunk.total_chunks,
            chunk_text=chunk.text,
            content_type=chunk.content_type,
            pages=list(chunk.pages),
            related_article_ids=list(chunk.related_article_ids),
            related_incident_ids=list(chunk.related_incident_ids),
            related_problem_ids=list(chunk.related_problem_ids),
            related_known_error_ids=list(chunk.related_known_error_ids),
            related_change_ids=list(chunk.related_change_ids),
            related_mir_ids=list(chunk.related_mir_ids),
        )

    def to_qdrant_payload(self) -> dict[str, Any]:
        """Serialize for Qdrant: enums as values, unset optionals omitted."""
        return self.model_dump(mode="json", exclude_none=True)
