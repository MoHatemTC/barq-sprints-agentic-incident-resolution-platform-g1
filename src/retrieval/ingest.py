"""Re-export shim for the ingestion pipeline."""

from app.retrieval.ingest import (
    KB_NAMESPACE,
    build_point_id,
    ingest_articles,
)

__all__ = [
    "KB_NAMESPACE",
    "build_point_id",
    "ingest_articles",
]
