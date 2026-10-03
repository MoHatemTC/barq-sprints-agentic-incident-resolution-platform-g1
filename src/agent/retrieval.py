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
filter, and the evidence gate (§11.7 ``threshold``) is applied to that. Reranker scores only
order candidates; a sigmoid is not a calibrated probability.
The dense evidence threshold stays separate from reranker ordering.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

from qdrant_client import QdrantClient
from qdrant_client.models import Condition, FieldCondition, Filter, MatchAny, MatchValue

from agent.diversity import mmr_order
from agent.state import EvidenceItem, RetrievalResult
from app.clients.qdrant import DENSE_VECTOR_NAME
from app.core.config import RetrievalMode, get_retrieval_settings
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
    _validate_hit,
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

#: Categories excluded from incident retrieval (e.g. operations manual process sections).
EXCLUDED_CATEGORIES: list[str] = ["process"]

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
        settings = get_retrieval_settings()
        reranked = settings.retrieval_mode == RetrievalMode.HYBRID_RERANKED

        mmr_applied = False

        def rank_score(item: EvidenceItem) -> float:
            return item.fused_score if reranked else item.relevance

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
                mmr_applied=mmr_applied,
                mmr_lambda=settings.retrieval_mmr_lambda
                if settings.retrieval_mmr_enabled
                else None,
            )

        wide_extra = (
            Filter(
                must_not=[FieldCondition(key="category", match=MatchAny(any=EXCLUDED_CATEGORIES))]
            )
            if EXCLUDED_CATEGORIES
            else None
        )

        if not categories and (
            not settings.retrieval_mmr_enabled or settings.retrieval_mmr_lambda == 1
        ):
            # No corpus category covers this label (security, other), so there is no
            # precise pass to run. The wide pass still has to run: S3.5 captures a
            # human solution out of exactly this population (the capture trigger is
            # the no-evidence interrupt), so returning early made every article the
            # platform learned unreachable to the incidents that produced it.
            wide, _ = self._one_pass(query, wide_extra, top_k=top_k, engine=engine)
            items = sorted(wide, key=rank_score, reverse=True)[:top_k]
            sufficient = any(
                item.relevance >= threshold + OUT_OF_CATEGORY_EVIDENCE_MARGIN for item in items
            )
            return result(None, items, sufficient=sufficient)

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
        candidate_k = max(top_k * 2, 10)
        extra = (
            Filter(must=[FieldCondition(key="category", match=MatchAny(any=categories))])
            if categories
            else None
        )
        scoped: list[EvidenceItem] = []
        if extra is not None:
            scoped, _ = self._one_pass(query, extra, top_k=candidate_k, engine=engine)
        wide, _ = self._one_pass(query, wide_extra, top_k=candidate_k, engine=engine)

        in_category = {(item.article_id, item.chunk_index) for item in scoped}
        merged: dict[tuple[str, int], EvidenceItem] = {}
        for item in [*scoped, *wide]:
            key = (item.article_id, item.chunk_index)
            kept = merged.get(key)
            if kept is None or rank_score(item) > rank_score(kept):
                merged[key] = item

        client = self._qdrant()

        # Group-by-KB Parent-Document Bundling:
        # Group chunks by article and rank articles by their highest-relevance chunk.
        by_article: dict[str, list[EvidenceItem]] = {}
        for item in merged.values():
            by_article.setdefault(item.article_id, []).append(item)

        ranked_articles = sorted(
            by_article.keys(),
            key=lambda a: max(rank_score(c) for c in by_article[a]),
            reverse=True,
        )

        if settings.retrieval_mmr_enabled and settings.retrieval_mmr_lambda < 1:
            # Diversify only articles that already clear the existing evidence
            # gate. Other articles retain their rank slots; novelty cannot make
            # weak evidence eligible. Select whole articles before adding their
            # diagnostic/Resolution companions so MMR never penalizes siblings.
            representatives = {
                article: max(by_article[article], key=rank_score)
                for article in ranked_articles
                if any(
                    chunk.relevance
                    >= (
                        threshold
                        if (chunk.article_id, chunk.chunk_index) in in_category
                        else threshold + OUT_OF_CATEGORY_EVIDENCE_MARGIN
                    )
                    for chunk in by_article[article]
                )
            }
            if len(representatives) > 1:
                scores = {
                    article: max(0.0, min(1.0, rank_score(chunk)))
                    for article, chunk in representatives.items()
                }
                vectors = self._representative_vectors(client, representatives)
                try:
                    diversified = iter(
                        mmr_order(scores, vectors, lambda_mult=settings.retrieval_mmr_lambda)
                    )
                except ValueError as exc:
                    raise TerminalError("MMR dense vector contract violated") from exc
                mmr_applied = True
                ranked_articles = [
                    next(diversified) if article in representatives else article
                    for article in ranked_articles
                ]

        bundled_items: list[EvidenceItem] = []
        seen_keys: set[tuple[str, int]] = set()

        for art_id in ranked_articles:
            art_chunks = by_article[art_id]
            best_chunk = max(art_chunks, key=rank_score)

            # Ensure resolution chunk is present for any relevant article
            res_chunk = next(
                (c for c in art_chunks if c.section.strip().lower() == "resolution"), None
            )
            if res_chunk is None and best_chunk.relevance >= threshold:
                res_chunk = self._fetch_resolution_chunk(client, art_id, engine, query, extra)
                if res_chunk is not None:
                    by_article[art_id].append(res_chunk)

            # 1. Add the best diagnostic chunk (e.g. Symptom or Cause)
            best_key = (best_chunk.article_id, best_chunk.chunk_index)
            if best_key not in seen_keys:
                bundled_items.append(best_chunk)
                seen_keys.add(best_key)

            # 2. Add the Resolution chunk so the Resolution Agent has actionable steps
            if res_chunk is not None:
                res_key = (res_chunk.article_id, res_chunk.chunk_index)
                if res_key not in seen_keys:
                    bundled_items.append(res_chunk)
                    seen_keys.add(res_key)

            if len(bundled_items) >= top_k:
                break

        # Fill remaining capacity with any other high-relevance chunks if under top_k
        if len(bundled_items) < top_k:
            remaining = sorted(
                [
                    item
                    for item in merged.values()
                    if (item.article_id, item.chunk_index) not in seen_keys
                ],
                key=rank_score,
                reverse=True,
            )
            for item in remaining:
                bundled_items.append(item)
                seen_keys.add((item.article_id, item.chunk_index))
                if len(bundled_items) >= top_k:
                    break

        items = bundled_items[:top_k]

        sufficient = any(
            item.relevance
            >= (
                threshold
                if (item.article_id, item.chunk_index) in in_category
                else threshold + OUT_OF_CATEGORY_EVIDENCE_MARGIN
            )
            for item in items
        )
        return result(",".join(categories) or None, items, sufficient=sufficient)

    def _representative_vectors(
        self, client: QdrantClient, representatives: dict[str, EvidenceItem]
    ) -> dict[str, list[float]]:
        """Read indexed vectors for exact representative versions/chunks in one pool.

        Do not re-embed or read out-of-tier records. Missing vectors are a contract
        failure, and transport outages are retried rather than silently claiming
        diversity succeeded.
        """
        clauses: list[Condition] = [
            Filter(
                must=[
                    FieldCondition(
                        key="article_number", match=MatchValue(value=item.article_number)
                    ),
                    FieldCondition(key="version", match=MatchValue(value=item.version)),
                    FieldCondition(key="chunk_index", match=MatchValue(value=item.chunk_index)),
                ]
            )
            for item in representatives.values()
        ]
        scoped = Filter(
            must=[_mandatory_filter(None, self._max_security_level), Filter(should=clauses)]
        )
        vectors: dict[str, list[float]] = {}
        expected = {
            (item.article_number, item.version, item.chunk_index): article
            for article, item in representatives.items()
        }
        try:
            offset = None
            while True:
                points, offset = client.scroll(
                    collection_name=self._collection_name or _get_default_collection_name(),
                    scroll_filter=scoped,
                    limit=max(1, len(representatives)),
                    offset=offset,
                    with_payload=["article_number", "version", "chunk_index"],
                    with_vectors=[DENSE_VECTOR_NAME],
                )
                for point in points:
                    payload = point.payload or {}
                    key = (
                        payload.get("article_number"),
                        payload.get("version"),
                        payload.get("chunk_index"),
                    )
                    if not (
                        isinstance(key[0], str)
                        and isinstance(key[1], str)
                        and isinstance(key[2], int)
                    ):
                        continue
                    article = expected.get((key[0], key[1], key[2]))
                    if article is not None and isinstance(point.vector, dict):
                        vector = point.vector.get(DENSE_VECTOR_NAME)
                        if isinstance(vector, list) and all(
                            isinstance(v, (int, float)) for v in vector
                        ):
                            vectors[article] = [
                                float(v) for v in vector if isinstance(v, (int, float))
                            ]
                if offset is None:
                    break
        except Exception as exc:
            raise RetryableError(f"MMR vectors unavailable: {type(exc).__name__}") from exc
        if set(vectors) != set(representatives):
            raise TerminalError("MMR representative dense vectors missing from permitted index")
        return vectors

    def _fetch_resolution_chunk(
        self,
        client: QdrantClient,
        article_id: str,
        engine: EmbeddingEngine,
        query: str,
        extra: Filter | None,
    ) -> EvidenceItem | None:
        """Fetch the Resolution chunk for a matched article if not present in search hits."""
        try:
            art_num = article_id.split("-v")[0]
            ver = article_id.split("-v")[1] if "-v" in article_id else None
            must_clauses: list[Condition] = [
                FieldCondition(key="article_number", match=MatchValue(value=art_num)),
                FieldCondition(key="section", match=MatchValue(value="Resolution")),
                _mandatory_filter(None, self._max_security_level),
            ]
            if ver:
                must_clauses.append(FieldCondition(key="version", match=MatchValue(value=ver)))
            res_filter = Filter(must=must_clauses)
            response, _ = client.scroll(
                collection_name=self._collection_name or _get_default_collection_name(),
                scroll_filter=res_filter,
                limit=1,
                with_payload=True,
                with_vectors=False,
            )
            if not response:
                return None
            hit = _validate_hit(response[0])
            relevance_map = self._dense_scores(client, engine.embed_query(query), None, [hit])
            return EvidenceItem(
                article_id=hit.article_id,
                article_number=hit.article_number,
                version=hit.version,
                title=hit.title,
                section=hit.section,
                chunk_index=hit.chunk_index,
                text=hit.chunk_text,
                fused_score=hit.score,
                relevance=relevance_map.get((hit.article_id, hit.chunk_index), 0.0),
                category=hit.category,
            )
        except Exception:
            return None

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
                relevance=relevance.get((hit.article_id, hit.chunk_index), 0.0),
                category=hit.category,
            )
            for hit in hits
        ]
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
            key = (
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
    "EXCLUDED_CATEGORIES",
    "OUT_OF_CATEGORY_EVIDENCE_MARGIN",
    "QdrantRetriever",
    "Retriever",
    "build_default_retriever",
    "search_categories",
]
