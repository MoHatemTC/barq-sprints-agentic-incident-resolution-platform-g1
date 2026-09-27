"""Evidence retrieval for the ``retrieve`` node (S2.5 ↔ S2.4 seam).

The node depends on the :class:`Retriever` protocol. The default implementation uses
S2.4's hybrid entry point — dense + BM25 fused with RRF, published-only, security-tier
filtered — reached through :func:`_run_search`, which accepts either
``app.retrieval.search.retrieve_knowledge`` (pre-#110) or
``app.retrieval.hybrid_search.hybrid_search`` (#110 onwards).

**Why a second score.** RRF scores are rank sums: the top hit of *any* query scores
the same whether or not it is relevant, so they cannot answer "is there evidence at
all?" (manual §11.4 "Escalated — no evidence"). The retriever therefore also asks
Qdrant for the dense cosine similarity of the same chunks, under the same mandatory
filter, and the evidence gate (§11.7 ``threshold``) is applied to that. When S2.4's
reranker lands (#110), its calibrated score can replace the cosine without changing
this interface.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

from qdrant_client import QdrantClient
from qdrant_client.models import Condition, FieldCondition, Filter, MatchAny, MatchValue

from agent.state import EvidenceItem, RetrievalResult
from app.clients.qdrant import DENSE_VECTOR_NAME
from app.models.knowledge import (
    CORPUS_CATEGORY_TO_CLASSIFICATION,
    Classification,
    SecurityLevel,
)
from app.retrieval.embedding import EmbeddedText, EmbeddingEngine
from app.retrieval.filters import (
    MetadataFilterBuilder,
    build_metadata_filter,
)
from app.retrieval.hybrid_search import (
    _get_default_collection_name,
    hybrid_search,
)
from app.workers.retry_policy import RetryableError, TerminalError


def _run_search(
    client: QdrantClient,
    query: str,
    *,
    collection_name: str | None,
    limit: int,
    extra_filter: Filter | None,
    max_security_level: SecurityLevel,
    engine: EmbeddingEngine,
) -> list[Any]:
    """Hybrid search through the S2.4 query engine."""
    return hybrid_search(
        client,
        query,
        collection_name=collection_name,
        limit=limit,
        metadata=MetadataFilterBuilder(max_security_level=max_security_level),
        extra_filter=extra_filter,
        engine=engine,
    )


def _mandatory_filter(extra: Filter | None, max_security_level: SecurityLevel) -> Filter:
    """The published-only, security-tiered filter, plus ``extra``."""
    return build_metadata_filter(
        metadata=MetadataFilterBuilder(max_security_level=max_security_level),
        extra=extra,
    )


#: Extra relevance an out-of-category article must carry to count as evidence.
#:
#: Both searches run, so every incident now sees the whole corpus — including ones
#: no article covers, whose nearest match is by construction stronger than it was
#: under the category filter. Out-of-category hits therefore have to clear a higher
#: bar before they satisfy the §11.7 gate. They are still handed to the model either
#: way: being visible to ``verify_evidence`` is what fixes the misclassification, and
#: the gate is what stops a junk match becoming a draft.
OUT_OF_CATEGORY_EVIDENCE_MARGIN = 0.1


def _evidence_key(article_id: str, chunk_index: int) -> tuple[str, int]:
    """The one place a hit is identified for the dense re-scoring pass.

    Both sides of that pass must agree on this tuple: the search returns hits keyed
    by the composition S2.4 puts in ``article_id``, and the re-scoring query
    rebuilds it from the stored payload. Composing it in two places let the formats
    drift, at which point every relevance silently became 0.0 and every incident
    escalated as "no evidence" — a scoring bug indistinguishable from a policy
    decision. One function, one rule.
    """
    return (article_id, chunk_index)


#: Classification label → corpus category (inverse of the S1.4 mapping, #70).
CLASSIFICATION_TO_CORPUS_CATEGORY: dict[Classification, str] = {
    label: category for category, label in CORPUS_CATEGORY_TO_CLASSIFICATION.items()
}


class Retriever(Protocol):
    def search(
        self,
        query: str,
        *,
        classification: Classification,
        top_k: int,
        threshold: float,
        incident_category: str | None = None,
    ) -> RetrievalResult: ...


def search_categories(classification: Classification, incident_category: str | None) -> list[str]:
    """Corpus categories to search.

    The model's label decides whether there can be evidence at all: ``security`` and
    ``other`` have no corpus article. Otherwise the incident's own ServiceNow category
    (already checked against the supported set) is searched too — a VPN failure that
    the model calls ``access`` is still filed under ``network``, where KB0001 lives
    (observed with Gemini on 2026-09-17).
    """
    mapped = CLASSIFICATION_TO_CORPUS_CATEGORY.get(classification)
    if mapped is None:
        return []
    categories = [mapped]
    corpus = set(CLASSIFICATION_TO_CORPUS_CATEGORY.values())
    if incident_category in corpus and incident_category not in categories:
        categories.append(incident_category)
    return categories


class _MemoEngine:
    """Embeds each query once even though two searches need it."""

    def __init__(self, inner: EmbeddingEngine) -> None:
        self._inner = inner
        self._cache: dict[str, EmbeddedText] = {}

    @property
    def dense_vector_size(self) -> int:
        return self._inner.dense_vector_size

    def embed_documents(self, texts: list[str]) -> list[EmbeddedText]:
        return self._inner.embed_documents(texts)

    def embed_query(self, text: str) -> EmbeddedText:
        if text not in self._cache:
            self._cache[text] = self._inner.embed_query(text)
        return self._cache[text]


class QdrantRetriever:
    def __init__(
        self,
        client_factory: Callable[[], QdrantClient],
        engine_factory: Callable[[], EmbeddingEngine],
        *,
        collection_name: str | None = None,
        max_security_level: SecurityLevel = SecurityLevel.INTERNAL,
    ) -> None:
        self._client_factory = client_factory
        self._engine_factory = engine_factory
        self._client: QdrantClient | None = None
        self._collection_name = collection_name
        self._max_security_level = max_security_level

    def _qdrant(self) -> QdrantClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def search(
        self,
        query: str,
        *,
        classification: Classification,
        top_k: int,
        threshold: float,
        incident_category: str | None = None,
    ) -> RetrievalResult:
        engine = _MemoEngine(self._engine_factory())
        started = time.perf_counter()
        categories = search_categories(classification, incident_category)

        def result(
            label: str | None,
            items: list[EvidenceItem],
            *,
            sufficient: bool,
        ) -> RetrievalResult:
            return RetrievalResult(
                query=query,
                category_filter=label,
                hits=items,
                best_relevance=max((item.relevance for item in items), default=0.0),
                threshold=threshold,
                sufficient=sufficient,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )

        if not categories:
            # No corpus category covers this label (security, other). An unfiltered
            # search would still return the "nearest" article — measured at 0.58 for
            # a leave request — so there is, by definition, no evidence.
            return result(None, [], sufficient=False)

        # Two searches, always, and the union of what they return.
        #
        # The category pass is the precise one and keeps the S2.4 baseline intact.
        # It is not sufficient on its own: the category comes from the model, and a
        # wrong label removes the right article before the search runs. Worse, the
        # filtered pass can succeed *with the wrong article* — 'Outlook shows
        # Disconnected' labelled 'network' returned KB0003 at 0.67, over the 0.55
        # threshold, so no fallback keyed on "found nothing" would ever fire, while
        # KB0002 sat unreachable (dev407364, 2026-09-20). Searching the whole
        # published, in-tier corpus alongside it puts both candidates in front of the
        # ranking and lets relevance decide.
        extra = Filter(must=[FieldCondition(key="category", match=MatchAny(any=categories))])
        scoped, _ = self._one_pass(query, extra, top_k=top_k, engine=engine)
        wide, _ = self._one_pass(query, None, top_k=top_k, engine=engine)

        in_category = {(item.article_id, item.chunk_index) for item in scoped}
        merged: dict[tuple[str, int], EvidenceItem] = {}
        for item in [*scoped, *wide]:
            key = (item.article_id, item.chunk_index)
            kept = merged.get(key)
            if kept is None or item.relevance > kept.relevance:
                merged[key] = item
        items = sorted(merged.values(), key=lambda item: item.relevance, reverse=True)[:top_k]

        sufficient = any(
            item.relevance
            >= (
                threshold
                if (item.article_id, item.chunk_index) in in_category
                else threshold + OUT_OF_CATEGORY_EVIDENCE_MARGIN
            )
            for item in items
        )
        return result(",".join(categories), items, sufficient=sufficient)

    def _one_pass(
        self,
        query: str,
        extra: Filter | None,
        *,
        top_k: int,
        engine: EmbeddingEngine,
    ) -> tuple[list[EvidenceItem], float]:
        try:
            client = self._qdrant()
            hits = _run_search(
                client,
                query,
                collection_name=self._collection_name,
                limit=top_k,
                extra_filter=extra,
                max_security_level=self._max_security_level,
                engine=engine,
            )
            relevance = self._dense_scores(client, engine.embed_query(query), extra, list(hits))
        except ValueError as exc:
            # Malformed payloads violate the ingestion contract: retrying cannot help.
            raise TerminalError(f"retrieval contract violated: {exc}") from exc
        except Exception as exc:
            # Qdrant unreachable / timed out: the next attempt may succeed.
            raise RetryableError(f"retrieval unavailable: {type(exc).__name__}") from exc

        items = [
            EvidenceItem(
                article_id=hit.article_id,
                article_number=hit.article_number,
                version=hit.version,
                title=hit.title,
                section=hit.section,
                chunk_index=hit.chunk_index,
                text=hit.chunk_text,
                fused_score=hit.score,
                relevance=relevance.get(_evidence_key(hit.article_id, hit.chunk_index), 0.0),
            )
            for hit in hits
        ]
        # A hit the dense pass never returned a score for means the key built here
        # and the key built in _dense_scores disagree — a composition-rule drift, not
        # a weak result. Defaulting those to 0.0 would leave every incident scoring
        # 0.0 relevance, which reads downstream as "no evidence" and escalates the
        # whole queue to a human with a trace that looks like a healthy search. Fail
        # loudly instead. Presence is checked, not the value: a genuine cosine of 0.0
        # is clamped to 0.0 and is a real score, not a missing one.
        missing = [
            hit.article_id
            for hit in hits
            if _evidence_key(hit.article_id, hit.chunk_index) not in relevance
        ]
        if missing:
            raise TerminalError(
                f"retrieval relevance could not be scored for {len(missing)} of "
                f"{len(hits)} hits (e.g. {missing[0]}): the article-id composition "
                "used by the dense pass no longer matches the one used to index the "
                "collection"
            )
        return items, max((item.relevance for item in items), default=0.0)

    def _dense_scores(
        self,
        client: QdrantClient,
        embedded: EmbeddedText,
        extra: Filter | None,
        hits: list[Any],
    ) -> dict[tuple[str, int], float]:
        """Dense cosine for exactly the chunks ``hits`` contains.

        Scoping this to the returned chunks rather than taking a dense top-N is
        what keeps every hit's relevance real. A fused hit that falls outside the
        dense top-N — routine once S2.4's reranker reorders the candidates, but
        possible with RRF alone — otherwise scored 0.0 by default and could drag
        ``best_relevance`` under the §11.7 threshold, escalating "no evidence" for
        an incident that had evidence.
        """
        if not hits:
            return {}
        chunk_clauses: list[Condition] = [
            Filter(
                must=[
                    FieldCondition(
                        key="article_number", match=MatchValue(value=hit.article_number)
                    ),
                    FieldCondition(key="version", match=MatchValue(value=hit.version)),
                    FieldCondition(key="chunk_index", match=MatchValue(value=hit.chunk_index)),
                ]
            )
            for hit in hits
        ]
        # Nest rather than splice: the mandatory filter keeps whatever shape S2.4
        # gives it, and the chunk clauses stay a self-contained "any of these".
        mandatory = _mandatory_filter(extra, self._max_security_level)
        scoped = Filter(must=[mandatory, Filter(should=chunk_clauses)])
        response = client.query_points(
            collection_name=self._collection_name or _get_default_collection_name(),
            query=embedded.dense,
            using=DENSE_VECTOR_NAME,
            query_filter=scoped,
            limit=len(hits),
            with_payload=["article_number", "version", "chunk_index"],
        )
        scores: dict[tuple[str, int], float] = {}
        for point in response.points:
            payload: dict[str, Any] = point.payload or {}
            key = _evidence_key(
                f"{payload.get('article_number')}-v{payload.get('version')}",
                int(payload.get("chunk_index", -1)),
            )
            scores[key] = max(0.0, min(1.0, float(point.score)))
        return scores


def build_default_retriever() -> Retriever:
    from agent.config import get_agent_settings
    from agent.llm import get_embedding_engine
    from app.clients.qdrant import get_qdrant_client

    return QdrantRetriever(
        get_qdrant_client,
        get_embedding_engine,
        max_security_level=SecurityLevel(get_agent_settings().agent_max_security_level),
    )


__all__ = [
    "CLASSIFICATION_TO_CORPUS_CATEGORY",
    "OUT_OF_CATEGORY_EVIDENCE_MARGIN",
    "QdrantRetriever",
    "Retriever",
    "build_default_retriever",
    "search_categories",
]
