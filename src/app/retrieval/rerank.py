from __future__ import annotations

import math
from functools import lru_cache

import structlog

from app.core.config import get_retrieval_settings
from app.retrieval.hybrid_search import RetrievalHit

logger = structlog.get_logger(__name__)


class CrossEncoderReranker:
    """
    Lazily-loaded cross-encoder reranker.

    Cross-encoder logits are normalized with a sigmoid before being stored
    as RetrievalHit.score so the score remains bounded to [0, 1].
    """

    def __init__(self, model_name: str | None = None) -> None:
        settings = get_retrieval_settings()
        self.model_name = model_name or settings.rerank_model
        self._model = None

    def _load(self):
        if self._model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            logger.info("rerank.loading_model", model=self.model_name)
            self._model = TextCrossEncoder(model_name=self.model_name)
        return self._model

    @staticmethod
    def _normalize_score(raw_score: float) -> float:
        """Convert a cross-encoder logit to a bounded [0, 1] score."""
        if raw_score >= 0:
            return 1.0 / (1.0 + math.exp(-raw_score))

        exp_score = math.exp(raw_score)
        return exp_score / (1.0 + exp_score)

    def rerank(self, query: str, hits: list[RetrievalHit], *, top_n: int) -> list[RetrievalHit]:
        """
        Rerank a list of RetrievalHits based on the query.
        """
        if not hits:
            return []
        if top_n <= 0:
            raise ValueError("top_n must be a positive integer")

        model = self._load()
        documents = [hit.chunk_text for hit in hits]
        raw_scores = list(model.rerank(query, documents))

        rescored = [
            hit.model_copy(update={"score": self._normalize_score(float(raw_score))})
            for hit, raw_score in zip(hits, raw_scores, strict=True)
        ]

        rescored.sort(key=lambda h: (-h.score, h.article_id, h.chunk_index))
        return rescored[:top_n]


@lru_cache
def get_default_reranker() -> CrossEncoderReranker:
    """Process-wide cached reranker, so the model is loaded at most once."""
    return CrossEncoderReranker()
