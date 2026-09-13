"""Retrieval pipeline: article sources, chunking, embedding, ingestion, and search."""

from app.retrieval.search import RetrievalHit, retrieve_knowledge

__all__ = ["RetrievalHit", "retrieve_knowledge"]
