"""Post-ingestion verification: expected points present and intact.

Replaces whole-collection count checks. A collection that also holds
human-captured articles or other non-seed records can never satisfy a count
equality, and a count can pass while an expected chunk is missing. The
verifier answers strictly for the articles it is given: every expected
deterministic point ID must exist with the chunk content that was seeded,
and anything else in the collection is ignored, not an error.
"""

from __future__ import annotations

import structlog
from qdrant_client import QdrantClient

from app.models.knowledge import Article
from app.retrieval.chunking import chunk_article
from app.retrieval.ingest import build_point_id

logger = structlog.get_logger(__name__)

DEFAULT_CHUNK_SIZE = 700
DEFAULT_CHUNK_OVERLAP = 120


class SeedVerificationError(RuntimeError):
    """Raised when the stored points do not match the seeded corpus."""


def verify_seeded_articles(
    client: QdrantClient,
    collection_name: str,
    articles: list[Article],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> None:
    """Fail loudly unless every seeded chunk is stored with its content.

    Re-derives the deterministic point IDs from the same chunking the
    ingestion used, then compares against the collection. Extra unrelated
    records (human-captured articles, legacy points) are deliberately
    ignored: the verifier's business is the seeded corpus, not the
    collection's total contents.
    """
    expected: dict[str, tuple[str, int, str]] = {}
    for article in articles:
        chunks = chunk_article(article, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        for chunk in chunks:
            point_id = build_point_id(article.article_id, chunk.chunk_index)
            expected[point_id] = (article.article_id, chunk.chunk_index, chunk.text)
    if not expected:
        raise SeedVerificationError("nothing to verify: the seeded corpus is empty")

    stored: dict[str, str] = {}
    offset: int | str | None = None
    while True:
        points, offset = client.scroll(
            collection_name=collection_name,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            if "chunk_text" in payload:
                stored[str(point.id)] = str(payload["chunk_text"])
        if offset is None:
            break

    def _describe(point_id: str) -> str:
        article_id, chunk_index, _ = expected[point_id]
        return f"{article_id} chunk {chunk_index} (point {point_id})"

    missing = sorted(set(expected) - set(stored))
    if missing:
        raise SeedVerificationError(
            f"{len(missing)} seeded chunk(s) missing from {collection_name!r}: "
            + ", ".join(_describe(point_id) for point_id in missing[:5])
            + ("…" if len(missing) > 5 else "")
        )

    changed = sorted(
        point_id for point_id, (_, _, text) in expected.items() if stored[point_id] != text
    )
    if changed:
        raise SeedVerificationError(
            f"{len(changed)} stored chunk(s) have different content than seeded: "
            + ", ".join(_describe(point_id) for point_id in changed[:5])
            + ("…" if len(changed) > 5 else "")
        )

    logger.info(
        "seed_verification_passed",
        collection=collection_name,
        articles=len(articles),
        points=len(expected),
    )
