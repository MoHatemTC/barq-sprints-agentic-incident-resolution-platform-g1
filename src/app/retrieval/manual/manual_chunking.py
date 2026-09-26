"""
Section-aware chunker for manual content.
"""

from __future__ import annotations

from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

from app.models.manual_section import ManualSection, ManualSectionChunk
from app.retrieval.chunking import balance_code_fences
from app.retrieval.extraction.parse_appendix import (
    AppendixERelationships,
    relationships_for_section,
)

DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 150


def _split_body(body: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    if len(body) <= chunk_size:
        return [body]
    splitter = RecursiveCharacterTextSplitter.from_language(
        language=Language.MARKDOWN,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    return balance_code_fences(splitter.split_text(body))


def chunk_section(
    section: ManualSection,
    relationships: AppendixERelationships,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[ManualSectionChunk]:
    """Chunk one section's body. Never produces a chunk crossing into another section."""
    body = section.body.strip()
    if not body:
        return []

    texts = [t.strip() for t in _split_body(body, chunk_size, chunk_overlap) if t.strip()]
    if not texts:
        return []

    related = relationships_for_section(relationships, section.section_number)
    total_chunks = len(texts)

    return [
        ManualSectionChunk(
            chunk_id=ManualSectionChunk.build_chunk_id(section.section_id, index),
            section_id=section.section_id,
            chunk_index=index,
            total_chunks=total_chunks,
            text=text,
            section_number=section.section_number,
            section_title=section.title,
            pages=section.pages,
            content_type=section.content_type,
            **related,
        )
        for index, text in enumerate(texts)
    ]


def chunk_sections(
    sections: list[ManualSection],
    relationships: AppendixERelationships,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[ManualSectionChunk]:
    """Chunk a batch of sections into a flat list of ManualSectionChunks."""
    chunks: list[ManualSectionChunk] = []
    for section in sections:
        chunks.extend(
            chunk_section(
                section,
                relationships=relationships,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
        )
    return chunks
