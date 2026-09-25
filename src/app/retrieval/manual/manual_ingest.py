"""Qdrant ingestion pipeline for manual section chunks."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import structlog
from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    FilterSelector,
    MatchAny,
    PointStruct,
    SparseVector,
)

from app.clients.qdrant import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME, ensure_collection
from app.models.manual_section import ManualSectionChunk, ManualSectionPayload
from app.retrieval.embedding import EmbeddedText, EmbeddingEngine, FastEmbedEngine

logger = structlog.get_logger(__name__)

DEFAULT_MANUAL_COLLECTION_NAME = "manual_sections"
DEFAULT_BATCH_SIZE = 64

MANUAL_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "barq-g1-manual-sections")


def build_section_point_id(section_id: str, chunk_index: int) -> str:
    """Deterministic UUIDv5 for a manual section chunk point (idempotent upserts)."""
    return str(uuid.uuid5(MANUAL_NAMESPACE, f"{section_id}::chunk::{chunk_index}"))


def embedding_text_for_chunk(chunk: ManualSectionChunk) -> str:
    """Text handed to the embedding model: section title prepended to the chunk."""
    return f"{chunk.section_number} {chunk.section_title}\n\n{chunk.text}"


def _delete_section_points(client: QdrantClient, name: str, section_ids: list[str]) -> None:
    """Delete every point belonging to the given section_ids before upserting fresh ones."""
    if not section_ids:
        return
    client.delete(
        collection_name=name,
        points_selector=FilterSelector(
            filter=Filter(must=[FieldCondition(key="section_id", match=MatchAny(any=section_ids))])
        ),
        wait=True,
    )


def _purge_unknown_section_points(client: QdrantClient, name: str, known_ids: set[str]) -> int:
    """Remove points for sections no longer present in the corpus (shrinkage reconciliation)."""
    stored_ids: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=name,
            limit=256,
            offset=offset,
            with_payload=["section_id"],
            with_vectors=False,
        )
        for point in points:
            section_id = (point.payload or {}).get("section_id")
            if section_id:
                stored_ids.add(str(section_id))
        if offset is None:
            break

    removed_ids = sorted(stored_ids - known_ids)
    if not removed_ids:
        return 0

    client.delete(
        collection_name=name,
        points_selector=FilterSelector(
            filter=Filter(must=[FieldCondition(key="section_id", match=MatchAny(any=removed_ids))])
        ),
        wait=True,
    )
    logger.info(
        "purged_points_from_removed_sections",
        count=len(removed_ids),
        sections=removed_ids,
    )
    return len(removed_ids)


def ingest_manual_sections(
    chunks: Sequence[ManualSectionChunk],
    client: QdrantClient,
    collection_name: str | None = None,
    embedding_engine: EmbeddingEngine | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    force_recreate: bool = False,
    purge_unknown_sections: bool = False,
) -> int:
    if not chunks:
        raise ValueError("ingest_manual_sections received no chunks; refusing to seed nothing")

    name = collection_name or DEFAULT_MANUAL_COLLECTION_NAME
    engine = embedding_engine or FastEmbedEngine()
    ensure_collection(
        client,
        name,
        dense_vector_size=engine.dense_vector_size,
        force_recreate=force_recreate,
    )

    # Single batch embedding across all chunk texts, exactly like the article
    # pipeline, so BM25 IDF weights are fitted across the true corpus.
    embedding_texts = [embedding_text_for_chunk(chunk) for chunk in chunks]
    embeddings: list[EmbeddedText] = engine.embed_documents(embedding_texts)

    points: list[PointStruct] = []
    for chunk, emb in zip(chunks, embeddings, strict=True):
        point_id = build_section_point_id(chunk.section_id, chunk.chunk_index)
        payload = ManualSectionPayload.from_chunk(chunk).to_qdrant_payload()
        points.append(
            PointStruct(
                id=point_id,
                vector={
                    DENSE_VECTOR_NAME: emb.dense,
                    SPARSE_VECTOR_NAME: SparseVector(
                        indices=emb.sparse_indices, values=emb.sparse_values
                    ),
                },
                payload=payload,
            )
        )

    section_ids = sorted({chunk.section_id for chunk in chunks})
    _delete_section_points(client, name, section_ids)
    if purge_unknown_sections:
        _purge_unknown_section_points(client, name, set(section_ids))

    total_upserted = 0
    for i in range(0, len(points), batch_size):
        batch = points[i : i + batch_size]
        client.upsert(collection_name=name, points=batch, wait=True)
        total_upserted += len(batch)

    logger.info("manual_points_upserted", count=total_upserted, collection=name)
    return total_upserted
