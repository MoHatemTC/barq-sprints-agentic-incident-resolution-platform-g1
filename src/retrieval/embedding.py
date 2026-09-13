"""Re-export shim for the embedding engine."""

from app.retrieval.embedding import (
    DENSE_VECTOR_SIZE,
    EmbeddedText,
    EmbeddingEngine,
    FastEmbedEngine,
)

__all__ = [
    "DENSE_VECTOR_SIZE",
    "EmbeddedText",
    "EmbeddingEngine",
    "FastEmbedEngine",
]
