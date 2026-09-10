"""Qdrant ingestion pipeline for incident knowledge articles.

Chunks articles, embeds them with one corpus-wide dual (dense+sparse) pass,
and upserts them with deterministic UUIDv5 point IDs so re-running the
pipeline overwrites points in place instead of duplicating them. Collection
setup is owned by :mod:`app.clients.qdrant` (``ensure_collection``) — the
single implementation of the collection/index contract.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, SparseVector

from app.clients.qdrant import ensure_collection
from app.models.knowledge import Article, KnowledgePayload
from app.retrieval.chunking import chunk_article
from app.retrieval.embedding import EmbeddedText, EmbeddingEngine, FastEmbedEngine

logger = logging.getLogger(__name__)

DEFAULT_COLLECTION_NAME = "incident_knowledge_base"
DEFAULT_BATCH_SIZE = 64

# Dedicated namespace (master plan §4.2): derives from the DNS namespace so
# KB point IDs never collide with any other uuid5(NAMESPACE_DNS, ...) use.
KB_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "barq-g1-kb")


def _get_default_collection_name() -> str:
    try:
        from app.core.config import get_retrieval_settings

        return get_retrieval_settings().qdrant_collection_name
    except Exception:
        return DEFAULT_COLLECTION_NAME


def build_point_id(article_id: str, chunk_index: int) -> str:
    """Deterministic UUIDv5 for an article chunk point (idempotent upserts)."""
    return str(uuid.uuid5(KB_NAMESPACE, f"{article_id}::chunk::{chunk_index}"))


def ingest_articles(
    articles: Sequence[Article],
    client: QdrantClient,
    collection_name: str | None = None,
    embedding_engine: EmbeddingEngine | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    chunk_size: int = 700,
    chunk_overlap: int = 120,
    force_recreate: bool = False,
) -> int:
    """Chunk, embed, and upsert articles into the Qdrant collection.

    Crucial: BM25 IDF fitting requires that all chunk texts are embedded
    together in a single batch call to embed_documents, preserving true
    corpus statistics.

    Raises:
        ValueError: If the input is empty or produces no chunks — seeding
            nothing is a configuration error, not a quiet no-op.

    Returns:
        The total count of chunk points upserted into Qdrant.
    """
    if not articles:
        raise ValueError("ingest_articles received no articles; refusing to seed nothing")

    name = collection_name or _get_default_collection_name()
    ensure_collection(client, name, force_recreate=force_recreate)

    engine = embedding_engine or FastEmbedEngine()

    chunk_records = []
    for article in articles:
        chunks = chunk_article(article, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        for chk in chunks:
            chunk_records.append((article, chk))

    if not chunk_records:
        raise ValueError(
            f"chunking produced no chunks from {len(articles)} articles; check the corpus bodies"
        )

    logger.info("Generated %d total chunks from %d articles", len(chunk_records), len(articles))

    # Single batch embedding across all chunk texts (fits corpus BM25 IDF)
    all_texts = [chk.text for _, chk in chunk_records]
    embeddings: list[EmbeddedText] = engine.embed_documents(all_texts)

    points: list[PointStruct] = []
    for (article, chk), emb in zip(chunk_records, embeddings, strict=True):
        point_id = build_point_id(article.article_id, chk.chunk_index)
        payload = KnowledgePayload.from_chunk(article, chk).to_qdrant_payload()

        points.append(
            PointStruct(
                id=point_id,
                vector={
                    "dense": emb.dense,
                    "sparse": SparseVector(
                        indices=emb.sparse_indices,
                        values=emb.sparse_values,
                    ),
                },
                payload=payload,
            )
        )

    total_upserted = 0
    for i in range(0, len(points), batch_size):
        batch = points[i : i + batch_size]
        # wait=True: the seed script's count verification must observe
        # completed writes, not in-flight ones.
        client.upsert(collection_name=name, points=batch, wait=True)
        total_upserted += len(batch)

    logger.info("Successfully upserted %d points to %s", total_upserted, name)
    return total_upserted
