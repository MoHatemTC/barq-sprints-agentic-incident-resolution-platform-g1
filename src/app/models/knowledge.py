"""Knowledge domain models shared by every stage of the retrieval pipeline.

`Article` is the contract between article sources and the pipeline: every source
(local JSON today, ServiceNow read-back and Path B supplied JSON later) must
normalize its data into this model, and chunking / embedding / ingestion only
ever see articles in this shape.

Vocabulary policy (decided for Path B readiness):
- ``workflow_state`` and ``security_level`` are closed enums — they mirror
  platform-level states and new values indicate a bug, not new data.
- ``category`` and ``service`` are an *open* controlled vocabulary — Path B
  data may legitimately introduce services we did not author for, so new
  slug-shaped values are accepted rather than rejected.
- Unknown extra fields are rejected. Normalization is the source's explicit
  job; this model never silently drops data it does not understand.
"""

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
VERSION_PATTERN = re.compile(r"^\d+\.\d+$")
BASE_ID_PATTERN = re.compile(r"^KB-[A-Z0-9]+(?:-[A-Z0-9]+)*$")


class WorkflowState(StrEnum):
    """Lifecycle states mirrored from the ServiceNow KB workflow."""

    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


class SecurityLevel(StrEnum):
    """Audience levels used for Sprint 2 metadata filtering."""

    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"


class Article(BaseModel):
    """A single knowledge base article in canonical Markdown.

    ``article_id`` carries the version suffix (``KB-DB-001-v2.0``) so version
    pairs coexist as distinct points in Qdrant, while ``base_id`` identifies
    the article across versions.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    base_id: str = Field(..., description="Version-less identifier, e.g. KB-DB-001")
    version: str = Field(..., pattern=VERSION_PATTERN, description="Article version, e.g. 1.0")
    article_id: str = Field(
        ...,
        description="Versioned identifier, e.g. KB-DB-001-v2.0 — must equal base_id-vversion",
    )
    title: str = Field(..., min_length=5, max_length=200)
    short_description: str = Field(
        ...,
        min_length=10,
        max_length=255,
        description="One-line summary; 255 chars matches the ServiceNow kb_knowledge limit",
    )
    category: str = Field(..., description="Open controlled vocabulary slug, e.g. database")
    service: str = Field(..., description="Open controlled vocabulary slug, e.g. postgresql")
    workflow_state: WorkflowState
    security_level: SecurityLevel
    content: str = Field(
        ...,
        min_length=50,
        description="Article body in canonical Markdown with fenced code blocks",
    )

    @field_validator("base_id")
    @classmethod
    def validate_base_id(cls, value: str) -> str:
        if not BASE_ID_PATTERN.match(value):
            raise ValueError(
                f"base_id must match {BASE_ID_PATTERN.pattern} (e.g. KB-DB-001), got {value!r}"
            )
        return value

    @field_validator("category", "service")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        if not SLUG_PATTERN.match(value):
            raise ValueError(
                f"vocabulary values must be lowercase slugs matching "
                f"{SLUG_PATTERN.pattern}, got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def validate_article_id(self) -> "Article":
        expected = f"{self.base_id}-v{self.version}"
        if self.article_id != expected:
            raise ValueError(f"article_id must be {expected!r}, got {self.article_id!r}")
        return self


class ArticleChunk(BaseModel):
    """One retrievable piece of an article, produced by the chunker."""

    model_config = ConfigDict(frozen=True)

    article_id: str
    chunk_index: int = Field(..., ge=0)
    total_chunks: int = Field(..., ge=1)
    section: str = Field(..., description="Header path the chunk belongs to, e.g. Resolution")
    text: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> "ArticleChunk":
        if self.chunk_index >= self.total_chunks:
            raise ValueError(
                f"chunk_index {self.chunk_index} must be < total_chunks {self.total_chunks}"
            )
        return self


class KnowledgePayload(BaseModel):
    """Schema of a Qdrant point payload.

    Carries the 5 mandatory metadata fields plus everything Sprint 2 and the
    approval UI need downstream: the retrievable text, chunk position, and —
    once the article has been round-tripped through ServiceNow — its ``sys_id``
    and permalink for citations.
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    article_id: str
    title: str

    # 5 mandatory metadata fields (payload-indexed in setup_qdrant.py)
    category: str
    service: str
    workflow_state: WorkflowState
    version: str
    security_level: SecurityLevel

    # Chunk position and retrievable text
    section: str
    chunk_index: int
    total_chunks: int
    chunk_text: str

    # Filled by the ServiceNow read-back source, None for local-only articles
    sys_id: str | None = None
    article_url: str | None = None

    @classmethod
    def from_chunk(cls, article: Article, chunk: ArticleChunk) -> "KnowledgePayload":
        """Build the payload for one chunk of an article (pre-ServiceNow)."""
        return cls(
            article_id=article.article_id,
            title=article.title,
            category=article.category,
            service=article.service,
            workflow_state=article.workflow_state,
            version=article.version,
            security_level=article.security_level,
            section=chunk.section,
            chunk_index=chunk.chunk_index,
            total_chunks=chunk.total_chunks,
            chunk_text=chunk.text,
        )

    def to_qdrant_payload(self) -> dict[str, str | int]:
        """Serialize for Qdrant: enums as values, unset optionals omitted."""
        return self.model_dump(mode="json", exclude_none=True)
