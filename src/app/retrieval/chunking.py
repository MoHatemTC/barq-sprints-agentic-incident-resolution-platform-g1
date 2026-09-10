"""Markdown chunking engine using LangChain text splitters.

Splits technical markdown articles along header boundaries (H1, H2, H3) while
preserving code blocks, technical tokens, and structural context. Long sections
exceeding chunk limits are recursively split using Markdown-aware character
splitting, and unclosed code fences are automatically balanced across chunk
boundaries (Option B pipeline).
"""

from langchain_text_splitters import (
    Language,
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from app.models.knowledge import Article, ArticleChunk

DEFAULT_CHUNK_SIZE = 1500
DEFAULT_CHUNK_OVERLAP = 150

HEADERS_TO_SPLIT_ON: list[tuple[str, str]] = [
    ("#", "Title"),
    ("##", "Section"),
    ("###", "Subsection"),
]


def extract_section_name(metadata: dict[str, str]) -> str:
    """Derive a canonical section name from header metadata."""
    if "Subsection" in metadata:
        if "Section" in metadata:
            return f"{metadata['Section']} > {metadata['Subsection']}"
        return metadata["Subsection"]
    if "Section" in metadata:
        return metadata["Section"]
    if "Title" in metadata:
        return "Overview"
    return "General"


def balance_code_fences(texts: list[str]) -> list[str]:
    """Ensure code blocks split across chunks have properly balanced fences.

    When a code block is cut across chunks, the opening chunk receives a closing
    fence (```) and the continuation chunk receives an opening fence with the
    original language tag (e.g. ```bash).
    """
    if len(texts) <= 1:
        return texts

    repaired: list[str] = []
    active_fence_tag: str | None = None

    for text in texts:
        if active_fence_tag:
            text = f"{active_fence_tag}\n" + text
            active_fence_tag = None

        fence_lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip().startswith("```") or line.strip().startswith("~~~")
        ]
        if len(fence_lines) % 2 != 0:
            active_fence_tag = fence_lines[-1]
            text = text.rstrip() + "\n```\n"

        repaired.append(text)

    return repaired


def chunk_markdown(
    content: str,
    article_id: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[ArticleChunk]:
    """Split canonical Markdown content into structured ArticleChunk pieces.

    Parameters
    ----------
    content:
        The markdown body to chunk.
    article_id:
        Identifier of the parent article (e.g. ``KB-DB-001-v2.0``).
    chunk_size:
        Target maximum character length for each chunk.
    chunk_overlap:
        Overlap character length when recursive splitting is required.

    Returns
    -------
    list[ArticleChunk]
        Sequential, bound-validated article chunks with section metadata.
    """
    if not content or not content.strip():
        return []

    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=HEADERS_TO_SPLIT_ON,
        strip_headers=False,
    )
    docs = header_splitter.split_text(content)
    if not docs:
        return []

    char_splitter = RecursiveCharacterTextSplitter.from_language(
        language=Language.MARKDOWN,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    raw_chunks: list[tuple[str, str]] = []
    for doc in docs:
        section = extract_section_name(doc.metadata)
        text = doc.page_content.strip()
        if not text:
            continue

        if len(text) > chunk_size:
            sub_splits = char_splitter.split_text(text)
            balanced_splits = balance_code_fences(sub_splits)
            for split_text in balanced_splits:
                cleaned = split_text.strip()
                if cleaned:
                    raw_chunks.append((section, cleaned))
        else:
            raw_chunks.append((section, text))

    if not raw_chunks:
        return []

    total_chunks = len(raw_chunks)
    return [
        ArticleChunk(
            article_id=article_id,
            chunk_index=i,
            total_chunks=total_chunks,
            section=sec,
            text=chunk_text,
        )
        for i, (sec, chunk_text) in enumerate(raw_chunks)
    ]


def chunk_article(
    article: Article,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[ArticleChunk]:
    """Chunk a validated Article model instance."""
    return chunk_markdown(
        content=article.content,
        article_id=article.article_id,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )


def chunk_articles(
    articles: list[Article],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[ArticleChunk]:
    """Chunk a batch of articles into a flat list of ArticleChunks."""
    chunks: list[ArticleChunk] = []
    for article in articles:
        chunks.extend(
            chunk_article(
                article=article,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
        )
    return chunks
