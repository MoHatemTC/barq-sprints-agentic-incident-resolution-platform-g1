"""Citation records for chat answers.

A citation is built from a ``RetrievalHit`` and carries everything the UI needs
to show the source: article identity, the manual section when the hit is manual
content, the chunk heading, and a verbatim excerpt. No field is ever derived by
guessing — in particular a source link is only present when a valid mapping
exists (none today), never fabricated.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.retrieval.hybrid_search import RetrievalHit

#: Payload category marking manual sections ingested by the manual pipeline
#: (``scripts/manual/pipeline_semantic_ingest.py``). Canonical incident articles
#: use incident categories and have no manual section.
MANUAL_CATEGORY = "process"

#: The manual's last numbered chapter is 12; lettered appendices continue from
#: here (A → 13), matching APPENDIX_BASE in the ingestion pipeline.
_APPENDIX_BASE = 13
_MAX_APPENDIX_MAJOR = _APPENDIX_BASE + 25  # A..Z

#: Display cap for the stored excerpt; full chunk text remains in Qdrant.
_EXCERPT_MAX_CHARS = 600


def manual_section_for_article_number(article_number: str) -> str | None:
    """Return the manual section number behind a manual article number.

    Exact inverse of ``article_number_for_section`` in the ingestion pipeline
    (``Document control`` → KB0000, ``3.4`` → KB0304, ``6`` → KB0600,
    ``B.3`` → KB1403) and validated against the corpus in tests. Returns
    ``None`` for anything that is not a manual-section article number —
    canonical incident articles (KB0001..KB0010) must never grow a
    fabricated manual section.
    """
    if not article_number.startswith("KB") or not article_number[2:].isdigit():
        return None
    number = int(article_number[2:])
    if number == 0:
        return "Document control"
    major, minor = divmod(number, 100)
    if major < 1 or major > _MAX_APPENDIX_MAJOR or minor > 99:
        return None
    if major >= _APPENDIX_BASE:
        letter = chr(ord("A") + major - _APPENDIX_BASE)
        return letter if minor == 0 else f"{letter}.{minor}"
    return str(major) if minor == 0 else f"{major}.{minor}"


def chunk_identity(article_id: str, chunk_index: int) -> str:
    """Stable evidence key the answering model cites and the verifier checks."""
    return f"{article_id}::chunk::{chunk_index}"


class Citation(BaseModel):
    """One validated source behind an answer."""

    model_config = ConfigDict(frozen=True)

    article_id: str
    article_number: str
    version: str
    title: str
    section: str = Field(description="Chunk heading as stored in the payload")
    chunk_index: int
    manual_section: str | None = Field(
        default=None, description="Manual section number, only for manual-category articles"
    )
    excerpt: str = Field(description="Verbatim supporting excerpt from the chunk")
    article_url: str | None = Field(
        default=None,
        description="Source link; only set when a valid mapping exists (never fabricated)",
    )


def build_citation(hit: RetrievalHit) -> Citation:
    manual_section = (
        manual_section_for_article_number(hit.article_number)
        if hit.category == MANUAL_CATEGORY
        else None
    )
    excerpt = hit.chunk_text
    if len(excerpt) > _EXCERPT_MAX_CHARS:
        excerpt = excerpt[:_EXCERPT_MAX_CHARS] + " …[truncated]"
    return Citation(
        article_id=hit.article_id,
        article_number=hit.article_number,
        version=hit.version,
        title=hit.title,
        section=hit.section,
        chunk_index=hit.chunk_index,
        manual_section=manual_section,
        excerpt=excerpt,
        article_url=None,
    )


def citation_label(citation: Citation) -> str:
    """The one-line source identity shown above the excerpt in the UI."""
    if citation.manual_section is not None:
        return f"{citation.article_number} — Manual §{citation.manual_section} — {citation.title}"
    return f"{citation.article_number} v{citation.version} — {citation.title} — §{citation.section}"


__all__ = [
    "MANUAL_CATEGORY",
    "Citation",
    "build_citation",
    "citation_label",
    "chunk_identity",
    "manual_section_for_article_number",
]
