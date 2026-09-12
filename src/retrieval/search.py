"""Re-export shim for the hybrid search entry point."""

from app.retrieval.search import (
    RetrievalHit,
    retrieve_knowledge,
)

__all__ = [
    "RetrievalHit",
    "retrieve_knowledge",
]
