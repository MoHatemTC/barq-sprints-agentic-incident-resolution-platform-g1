from __future__ import annotations

import math
from functools import lru_cache

import structlog

from app.core.config import get_retrieval_settings
from app.retrieval.hybrid_search import RetrievalHit

logger = structlog.get_logger(__name__)


RRF_K = 60  # standard RRF damping constant


class CrossEncoderReranker:
    """
    Lazily-loaded cross-encoder reranker with reciprocal rank fusion (RRF).

    Combines the cross-encoder ranking and the incoming retrieval fusion ranking
    using reciprocal rank fusion. The resulting `RetrievalHit.score` is an RRF
    rank score used for ordering candidates, not a calibrated relevance probability.
    """

    def __init__(self, model_name: str | None = None, rrf_weight: float | None = None) -> None:
        settings = get_retrieval_settings()
        self.model_name = model_name or settings.rerank_model
        # Weight of the incoming fusion ranking vs the cross-encoder ranking
        # in the final RRF. 0.0 = reranker has full authority (legacy behaviour).
        self.rrf_weight = settings.rerank_rrf_weight if rrf_weight is None else rrf_weight
        self.batch_size = settings.rerank_batch_size
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
        if not hits:
            return []
        if top_n <= 0:
            raise ValueError("top_n must be a positive integer")

        model = self._load()
        documents = [f"{hit.title}\n\n{hit.chunk_text}".strip() for hit in hits]
        raw_scores = list(model.rerank(query, documents, batch_size=self.batch_size))

        encoder_ranks = self._ranks(raw_scores)
        fusion_ranks = list(range(1, len(hits) + 1))  # input order = fusion order
        rescored = [
            hit.model_copy(
                update={
                    "score": (1 - self.rrf_weight) / (RRF_K + enc) + self.rrf_weight / (RRF_K + fus)
                }
            )
            for hit, enc, fus in zip(hits, encoder_ranks, fusion_ranks, strict=True)
        ]

        rescored.sort(key=lambda h: (-h.score, h.article_id, h.chunk_index))
        return rescored[:top_n]

    @staticmethod
    def _ranks(scores: list[float]) -> list[int]:
        """1-based ranks of the scores, highest first; ties keep input order."""
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        ranks = [0] * len(scores)
        for rank, index in enumerate(order, start=1):
            ranks[index] = rank
        return ranks


@lru_cache
def get_default_reranker() -> CrossEncoderReranker:
    """Process-wide cached reranker, so the model is loaded at most once."""
    return CrossEncoderReranker()
