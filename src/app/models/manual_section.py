from __future__ import annotations

import re
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

MANUAL_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "barq-g1-manual-sections")
SECTION_ID_PATTERN = re.compile(r"^section-[A-Za-z0-9][A-Za-z0-9._-]*$")


class ManualSectionType(StrEnum):
    """Content type classification for a manual section."""

    PROSE = "prose"
    TABLE = "table"
    OCR = "ocr"
    LAYOUT = "layout"


class ManualSection(BaseModel):
    """One logical section extracted from the BARQ Operations Manual.

    A section may span multiple pages and contains a single content type.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    section_id: str = Field(
        ...,
        description='Unique section identifier, e.g. "section-3.4"',
    )
    section_number: str = Field(
        ...,
        description='Section number as it appears in the manual, e.g. "3.4" or "E.1"',
    )
    title: str = Field(..., min_length=1, max_length=500)
    body: str = Field(
        ...,
        min_length=1,
        description="Extracted section content (prose, table markdown, OCR text, etc.)",
    )
    content_type: ManualSectionType
    pages: tuple[int, ...] = Field(
        ...,
        min_length=1,
        description="1-indexed page numbers this section spans",
    )
    ocr_confidence: float | None = Field(
        default=None,
        description="Mean OCR word confidence (0.0-1.0), only set for OCR sections",
    )
    reliability_note: str | None = Field(
        default=None,
        description="Human-readable note about extraction quality, if relevant",
    )

    @field_validator("section_id")
    @classmethod
    def validate_section_id(cls, value: str) -> str:
        if not SECTION_ID_PATTERN.match(value):
            raise ValueError(f"section_id must match {SECTION_ID_PATTERN.pattern}, got {value!r}")
        return value

    @field_validator("pages")
    @classmethod
    def validate_pages_positive(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(p < 1 for p in value):
            raise ValueError("page numbers must be >= 1")
        return value


class ManualSectionChunk(BaseModel):
    """One retrievable chunk of a manual section, produced by the section-aware chunker."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str = Field(
        ...,
        description='Deterministic chunk identifier, e.g. "section-3.4#c0"',
    )
    section_id: str
    chunk_index: int = Field(ge=0)
    total_chunks: int = Field(ge=1)
    text: str = Field(..., min_length=1)
    section_number: str
    section_title: str
    pages: tuple[int, ...]
    content_type: ManualSectionType

    # Relationship tuples populated from Appendix E
    related_article_ids: tuple[str, ...] = ()
    related_incident_ids: tuple[str, ...] = ()
    related_problem_ids: tuple[str, ...] = ()
    related_known_error_ids: tuple[str, ...] = ()
    related_change_ids: tuple[str, ...] = ()
    related_mir_ids: tuple[str, ...] = ()

    def model_post_init(self, __context: Any) -> None:
        if self.chunk_index >= self.total_chunks:
            raise ValueError(
                f"chunk_index {self.chunk_index} must be < total_chunks {self.total_chunks}"
            )


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

    # Relationship fields
    related_article_ids: list[str] = Field(default_factory=list)
    related_incident_ids: list[str] = Field(default_factory=list)
    related_problem_ids: list[str] = Field(default_factory=list)
    related_known_error_ids: list[str] = Field(default_factory=list)
    related_change_ids: list[str] = Field(default_factory=list)
    related_mir_ids: list[str] = Field(default_factory=list)

    @classmethod
    def from_chunk(
        cls,
        section: ManualSection,
        chunk: ManualSectionChunk,
    ) -> ManualSectionPayload:
        """Build the Qdrant payload for one chunk of a manual section."""
        return cls(
            section_id=section.section_id,
            section_number=section.section_number,
            section_title=section.title,
            chunk_index=chunk.chunk_index,
            total_chunks=chunk.total_chunks,
            chunk_text=chunk.text,
            content_type=section.content_type,
            pages=list(section.pages),
            ocr_confidence=section.ocr_confidence,
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


def build_section_point_id(section_id: str, chunk_index: int) -> str:
    """Deterministic UUIDv5 for a manual section chunk point (idempotent upserts)."""
    return str(uuid.uuid5(MANUAL_NAMESPACE, f"{section_id}::chunk::{chunk_index}"))
