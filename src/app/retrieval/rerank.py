from __future__ import annotations

from functools import lru_cache

import structlog

from app.core.config import get_retrieval_settings
from app.retrieval.hybrid_search import RetrievalHit

logger = structlog.get_logger(__name__)


class CrossEncoderReranker:
    """
    Lazily-loaded cross-encoder reranker
    """

    def __init__(self, model_name: str | None = None) -> None:
        settings = get_retrieval_settings()
        self.model_name = model_name or settings.rerank_model
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            logger.info("rerank.loading_model", model=self.model_name)
            self._model = CrossEncoder(self.model_name)
        return self._model

    def rerank(self, query: str, hits: list[RetrievalHit], *, top_n: int) -> list[RetrievalHit]:
        """
        Rerank a list of RetrievalHits based on the query.
        """
        if not hits:
            return []
        if top_n <= 0:
            raise ValueError("top_n must be a positive integer")

        model = self._load()
        pairs = [(query, hit.chunk_text) for hit in hits]
        raw_scores = model.predict(pairs)

        rescored = [
            hit.model_copy(update={"score": float(score)})
            for hit, score in zip(hits, raw_scores, strict=True)
        ]
        rescored.sort(key=lambda h: (-h.score, h.article_id, h.chunk_index))
        return rescored[:top_n]


@lru_cache
def get_default_reranker() -> CrossEncoderReranker:
    """Process-wide cached reranker, so the model is loaded at most once."""
    return CrossEncoderReranker()
