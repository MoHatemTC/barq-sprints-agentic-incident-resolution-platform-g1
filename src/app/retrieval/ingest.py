"""Qdrant ingestion pipeline for incident knowledge articles.

Sets up hybrid dense+sparse collections, creates payload indexes for fast filtering,
and batch-embeds article chunks using FastEmbedEngine.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PayloadSchemaType,
    PointStruct,
    SparseIndexParams,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from app.models.knowledge import Article, KnowledgePayload
from app.retrieval.chunking import chunk_article
from app.retrieval.embedding import (
    DENSE_VECTOR_SIZE,
    EmbeddedText,
    FastEmbedEngine,
)

logger = logging.getLogger(__name__)

DEFAULT_COLLECTION_NAME = "incident_knowledge_base"

PAYLOAD_INDEX_FIELDS: list[str] = [
    "article_number",
    "article_id",
    "category",
    "service",
    "workflow_state",
    "version",
    "security_level",
]


def _get_default_collection_name() -> str:
    try:
        from app.core.config import get_retrieval_settings

        return get_retrieval_settings().qdrant_collection_name
    except Exception:
        return DEFAULT_COLLECTION_NAME


def build_point_id(article_id: str, chunk_index: int) -> str:
    """Produce a deterministic UUID for an article chunk point using standard DNS namespace."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{article_id}#{chunk_index}"))


def setup_qdrant_collection(
    client: QdrantClient,
    collection_name: str | None = None,
    dense_dim: int = DENSE_VECTOR_SIZE,
    recreate: bool = False,
) -> None:
    """Create or recreate Qdrant collection with dense and sparse vectors and payload indexes."""
    name = collection_name or _get_default_collection_name()

    exists = client.collection_exists(collection_name=name)
    if exists and recreate:
        logger.info("Deleting existing collection %s", name)
        client.delete_collection(collection_name=name)
        exists = False

    if not exists:
        logger.info("Creating collection %s with dense+sparse vector configuration", name)
        client.create_collection(
            collection_name=name,
            vectors_config={
                "dense": VectorParams(
                    size=dense_dim,
                    distance=Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                "sparse": SparseVectorParams(
                    index=SparseIndexParams(on_disk=False),
                ),
            },
        )

        for field in PAYLOAD_INDEX_FIELDS:
            client.create_payload_index(
                collection_name=name,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD,
            )
        logger.info("Created keyword payload indexes for %s on %s", name, PAYLOAD_INDEX_FIELDS)


def ingest_articles(
    articles: Sequence[Article],
    client: QdrantClient,
    collection_name: str | None = None,
    embedding_engine: FastEmbedEngine | None = None,
    batch_size: int = 64,
    chunk_size: int = 700,
    chunk_overlap: int = 120,
) -> int:
    """Chunk, embed, and upsert articles into the Qdrant collection.

    Crucial: BM25 IDF fitting requires that all chunk texts are embedded together
    in a single batch call to embed_documents, preserving true corpus statistics.

    Returns:
        The total count of chunk points upserted into Qdrant.
    """
    name = collection_name or _get_default_collection_name()
    engine = embedding_engine or FastEmbedEngine()

    chunk_records = []
    for article in articles:
        chunks = chunk_article(article, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        for chk in chunks:
            chunk_records.append((article, chk, chk.text))

    if not chunk_records:
        logger.warning("No chunks generated from articles, aborting ingestion")
        return 0

    logger.info("Generated %d total chunks from %d articles", len(chunk_records), len(articles))

    # Single batch embedding across all chunk texts (fits corpus BM25 IDF)
    all_texts = [record[2] for record in chunk_records]
    embeddings: list[EmbeddedText] = engine.embed_documents(all_texts)

    points: list[PointStruct] = []
    for (article, chk, _), emb in zip(chunk_records, embeddings, strict=True):
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
        client.upsert(collection_name=name, points=batch)
        total_upserted += len(batch)

    logger.info("Successfully upserted %d points to %s", total_upserted, name)
    return total_upserted
