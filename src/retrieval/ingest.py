"""Re-export shim for the ingestion pipeline."""

from app.retrieval.ingest import (
    PAYLOAD_INDEX_FIELDS,
    build_point_id,
    ingest_articles,
    setup_qdrant_collection,
)

__all__ = [
    "PAYLOAD_INDEX_FIELDS",
    "build_point_id",
    "ingest_articles",
    "setup_qdrant_collection",
]
