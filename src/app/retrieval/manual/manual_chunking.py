"""
Section-aware chunker for manual content (Markdown pipeline).

Key design choices driven by the eval dataset:

1. **Prefer one chunk per section** — the evaluation asks questions scoped to
   individual sections (e.g. "3.4", "6.4 KB0001"). Splitting a KB article
   across multiple chunks means a retrieval hit on chunk 0 might miss the
   Resolution steps in chunk 1.  We therefore raise the budget to 2000 chars
   (~350–380 BAAI/bge-small-en-v1.5 tokens) so the vast majority of sections
   stay as a single chunk.

2. **Section title prefix on every chunk** — when a section IS split, each
   continuation chunk carries "§ X.Y Title:" as a prefix so the embedding
   model always understands what section the text belongs to, preserving
   retrieval fidelity even for split sections.

3. **Line-boundary splits for table content** — sections whose body is flat
   table rows (pipe-separated "Key: Value | …" lines) are split on newlines,
   not mid-row, so no single key-value pair is ever broken.

4. **No overlap for table sections** — overlapping table rows produces
   duplicated key-value pairs which confuse BM25 IDF weights.
"""

from __future__ import annotations

from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

from app.models.manual_section import ManualSection, ManualSectionChunk, ManualSectionType
from app.retrieval.chunking import balance_code_fences
from app.retrieval.extraction.parse_appendix import (
    AppendixERelationships,
    relationships_for_section,
)

DEFAULT_CHUNK_SIZE = 2000
DEFAULT_CHUNK_OVERLAP = 100

_NEWLINE_SEPARATORS = ["\n\n", "\n", " ", ""]


def _section_prefix(section: ManualSection) -> str:
    """Short prefix prepended to continuation chunks for context anchoring."""
    return f"§ {section.section_number} {section.title}:\n"


def _split_body(
    section: ManualSection,
    chunk_size: int,
    chunk_overlap: int,
) -> list[str]:
    """Split section body into chunks, respecting content-type boundaries."""
    body = section.body.strip()

    if len(body) <= chunk_size:
        return [body]

    is_table = section.content_type == ManualSectionType.TABLE

    if is_table:
        splitter = RecursiveCharacterTextSplitter(
            separators=_NEWLINE_SEPARATORS,
            chunk_size=chunk_size,
            chunk_overlap=0,
            keep_separator=False,
        )
    else:
        splitter = RecursiveCharacterTextSplitter.from_language(
            language=Language.MARKDOWN,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    raw_splits = splitter.split_text(body)

    if is_table:
        texts = [t.strip() for t in raw_splits if t.strip()]
    else:
        texts = balance_code_fences([t.strip() for t in raw_splits if t.strip()])

    if not texts:
        return [body]

    # Prepend section context to every continuation chunk (index ≥ 1).
    prefix = _section_prefix(section)
    return [texts[0]] + [prefix + t for t in texts[1:]]


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

    texts = _split_body(section, chunk_size, chunk_overlap)
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
