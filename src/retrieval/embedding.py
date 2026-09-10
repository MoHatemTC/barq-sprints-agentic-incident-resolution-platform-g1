"""Re-export shim for the retrieval package."""

from app.retrieval.embedding import (
    DEFAULT_DENSE_MODEL,
    DEFAULT_SPARSE_MODEL,
    DENSE_VECTOR_SIZE,
    DualEmbeddingEngine,
)

__all__ = [
    "DEFAULT_DENSE_MODEL",
    "DEFAULT_SPARSE_MODEL",
    "DENSE_VECTOR_SIZE",
    "DualEmbeddingEngine",
]
