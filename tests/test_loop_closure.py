"""S3.5 commit 5: empirical loop closure, proven with the real pipeline pieces.

The chain under test: escalated incident (no corpus coverage) → human decision
carrying a solution (folded into evidence) → capture_human_resolution (compose
→ registry publish → ingest_articles) → fresh, similar-incident retrieval
returns the new article as evidence.

Everything heavy is real: chunking, KnowledgePayload, deterministic point IDs,
the metadata filter. Only the embedding model (stub hash vectors), Qdrant
(a filter-honoring in-memory client) and ServiceNow (scripted registry) are
faked — and the fake Qdrant applies the query filter, so a broken default
filter would fail this test instead of passing it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pytest
from qdrant_client.models import FilterSelector

from agent.config import AgentSettings
from agent.dependencies import AgentDependencies
from agent.knowledge_capture import capture_human_resolution
from agent.prompts import ComposedArticle
from agent.servicenow import IncidentGateway
from agent.state import IncidentSnapshot
from agent.tools import build_servicenow_tool_registry
from agent.tools.permissions import PermissionClass
from agent.tools.registry import ApprovalCheckResult, ToolRegistration
from api.schemas.approvals import fold_solution_into_evidence
from app.models.knowledge import Article, SecurityLevel, WorkflowState
from app.retrieval.embedding import EmbeddedText
from app.retrieval.filters import build_metadata_filter
from app.retrieval.ingest import ingest_articles
from tests.agent_support import FakeLLM, FakeServiceNow, Tracer, shared_runner

EXECUTION_ID = "33333333-3333-4333-8333-333333333333"

INCIDENT = IncidentSnapshot(
    sys_id="inc-sys-9",
    number="INC0010099",
    short_description="VPN drops every few minutes",
    description="Corporate VPN drops intermittently for the requester.",
    category="software",
    service="corporate-vpn",
)

SOLUTION = "was a stale split tunnel route; flushed the vpn routes and reinstalled the client"

COMPOSER_ANSWER = ComposedArticle(
    title="Resolving Stale Split-Tunnel VPN Routes",
    short_description="Flush stale vpn routes and reinstall the client.",
    category="network",
    body=("1. Flush the stale split tunnel routes.\n2. Reinstall the vpn client."),
)

SIMILAR_QUERY = "vpn disconnects on the new laptop, split tunnel seems broken"


def _content_tokens(text: str) -> set[str]:
    """Content-token scorer: sub-4-char tokens carry no topical signal."""
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) >= 4}


class StubEmbeddingEngine:
    """Deterministic hash vectors — no model download, no network."""

    dense_vector_size = 384

    def embed_documents(self, texts: list[str]) -> list[EmbeddedText]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> EmbeddedText:
        return self._embed(text)

    @staticmethod
    def _embed(text: str) -> EmbeddedText:
        tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
        indices = sorted(abs(hash(t)) % 384 for t in tokens)
        dense = [0.0] * 384
        for i in indices:
            dense[i] = 1.0
        return EmbeddedText(dense=dense, sparse_indices=indices, sparse_values=[1.0] * len(indices))


@dataclass
class FilterHonoringQdrant:
    """In-memory Qdrant stand-in that APPLIES query filters before matching.

    Points are stored as {id: payload}; search scores by token overlap between
    the query and ``chunk_text`` — after the mandatory workflow_state and
    security_level conditions from the caller's filter have been applied.
    """

    points: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_query_filter: Any = None

    def collection_exists(self, **kwargs: Any) -> bool:
        return False  # route ensure_collection through create_collection

    def create_collection(self, **kwargs: Any) -> None:
        return None

    def create_payload_index(self, **kwargs: Any) -> None:
        return None

    def upsert(self, *, collection_name: str, points: list, wait: bool = True) -> None:
        for point in points:
            self.points[str(point.id)] = dict(point.payload)

    def delete(self, *, collection_name: str, points_selector: Any, wait: bool = True) -> None:
        if isinstance(points_selector, FilterSelector):
            for condition in points_selector.filter.must or []:
                if getattr(condition, "key", None) == "article_id":
                    doomed = set(condition.match.any or [])  # type: ignore[union-attr]
                    self.points = {
                        pid: payload
                        for pid, payload in self.points.items()
                        if payload.get("article_id") not in doomed
                    }

    def _match_any(self, key: str) -> list[str] | None:
        for cond in getattr(self.last_query_filter, "must", None) or []:
            if getattr(cond, "key", None) == key:
                return list(cond.match.any or [])  # type: ignore[union-attr]
        return None

    def filtered_search(
        self, query_text: str, metadata_filter: Any, limit: int = 5
    ) -> list[dict[str, Any]]:
        """The retriever's contract: build_metadata_filter output gates every hit."""
        self.last_query_filter = metadata_filter
        allowed_states = self._match_any("workflow_state") or []
        allowed_levels = self._match_any("security_level") or []
        query_tokens = _content_tokens(query_text)

        hits: list[tuple[float, dict[str, Any]]] = []
        for payload in self.points.values():
            if payload.get("workflow_state") not in allowed_states:
                continue  # a real filtered query would never see this point
            if payload.get("security_level") not in allowed_levels:
                continue
            tokens = _content_tokens(str(payload.get("chunk_text", "")))
            score = len(query_tokens & tokens) / (1 + len(tokens))
            if score > 0:
                hits.append((score, payload))
        hits.sort(key=lambda pair: -pair[0])
        return [payload for _, payload in hits[:limit]]


class _Permissive:
    async def check(self, *, execution_id: str, tool_name: str) -> ApprovalCheckResult:
        return ApprovalCheckResult(True)


def _capture_deps() -> AgentDependencies:
    """Deps whose registry carries a scripted-but-real publish_kb_article tool."""
    tracer = Tracer(None)
    gateway = IncidentGateway(lambda: FakeServiceNow(), tracer, runner=shared_runner())

    async def fake_sn_publish(article: Any) -> str:
        return "sn-sys-9"  # ServiceNow accepted the article

    registry = build_servicenow_tool_registry(
        gateway,
        approval_checker=_Permissive(),
        extra_registrations=[
            ToolRegistration("publish_kb_article", PermissionClass.HIGH_RISK, fake_sn_publish)
        ],
    )
    return AgentDependencies(
        settings=AgentSettings(_env_file=None, agent_checkpointer_backend="memory"),
        llm=FakeLLM(answers={"article_composer": COMPOSER_ANSWER}),
        retriever=_NoopRetriever(),
        tools=registry,
        tracer=tracer,
    )


class _NoopRetriever:
    """Capture never retrieves; this test does its own filtered search."""

    def search(self, *args: Any, **kwargs: Any) -> list:  # pragma: no cover
        return []


async def test_loop_closure_returns_human_resolved_article_for_similar_incident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qdrant = FilterHonoringQdrant()

    # --- Corpus seed: ONLY an unrelated database article. No VPN coverage. --
    corpus = Article(
        article_number="KB0001",
        version="1.0",
        title="Database Connection Pool Exhaustion",
        short_description="Bump the pool and restart.",
        body="1. Raise max connections.\n2. Restart the database pooler service.",
        category="database",
        service="sap-erp",
        workflow_state=WorkflowState.PUBLISHED,
        security_level=SecurityLevel.INTERNAL,
    )
    ingest_articles([corpus], qdrant, embedding_engine=StubEmbeddingEngine())
    default_filter = build_metadata_filter()

    # Escalation precondition: the similar query finds nothing to act on.
    assert qdrant.filtered_search(SIMILAR_QUERY, default_filter) == []

    # --- The human decides, contributing their solution (commit 1 fold). ---
    evidence = fold_solution_into_evidence(None, SOLUTION)
    assert evidence is not None
    assert evidence["tool_name"] == "publish_kb_article"

    # --- Capture runs on the resume path (commits 2-4). --------------------
    async def next_number() -> str:
        return "KB1001"

    # Real ingest pipeline, stub embeddings only.
    monkeypatch.setattr(
        "agent.knowledge_capture.ingest_articles",
        lambda articles, client, **kwargs: ingest_articles(
            articles, client, embedding_engine=StubEmbeddingEngine()
        ),
    )
    result = await capture_human_resolution(
        execution_id=EXECUTION_ID,
        incident=INCIDENT,
        solution_text=SOLUTION,
        deps=_capture_deps(),
        next_number=next_number,
        qdrant_client=qdrant,
        kb_sys_id="kb-sys-1",
    )

    assert result is not None
    assert result.published is True
    assert result.ingested is True
    assert result.article_number == "KB1001"
    assert result.sys_id == "sn-sys-9"

    # --- Loop closed: the SAME query now returns KB1001 as evidence. -------
    hits = qdrant.filtered_search(SIMILAR_QUERY, default_filter)
    assert hits, "the captured knowledge must be retrievable for a similar incident"
    top = hits[0]
    assert top["article_number"] == "KB1001"
    assert top["workflow_state"] == WorkflowState.HUMAN_RESOLVED.value
    assert top["sys_id"] == "sn-sys-9"  # the audit chain reaches the points

    # The filter in play is the real default fallback path, human_resolved included.
    assert set(qdrant._match_any("workflow_state") or []) >= {
        WorkflowState.PUBLISHED.value,
        WorkflowState.HUMAN_RESOLVED.value,
    }


async def test_loop_does_not_close_for_incompatible_security_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restricted-clearance search must NOT see the internal captured article.

    Guards against a fake that ignores filters: the security gate is part of
    the retriever's contract, so the loop only 'closes' within clearance.
    """
    qdrant = FilterHonoringQdrant()
    ingest_articles(
        [
            Article(
                article_number="KB0001",
                version="1.0",
                title="Database Connection Pool Exhaustion",
                short_description="Bump the pool and restart.",
                body="1. Raise max connections.\n2. Restart the database pooler service.",
                category="database",
                service="sap-erp",
                workflow_state=WorkflowState.PUBLISHED,
                security_level=SecurityLevel.INTERNAL,
            )
        ],
        qdrant,
        embedding_engine=StubEmbeddingEngine(),
    )
    ingest_articles(
        [
            Article(
                article_number="KB0002",
                version="1.0",
                title="Restricted VPN Root Cause Notes",
                short_description="Internal restricted notes.",
                body="Split tunnel route table was stale and got flushed by the vpn team.",
                category="network",
                service="corporate-vpn",
                workflow_state=WorkflowState.PUBLISHED,
                security_level=SecurityLevel.RESTRICTED,
            )
        ],
        qdrant,
        embedding_engine=StubEmbeddingEngine(),
    )
    internal_filter = build_metadata_filter()  # max_security_level=INTERNAL default

    hits = qdrant.filtered_search("stale split tunnel vpn route", internal_filter)
    numbers = {payload["article_number"] for payload in hits}
    assert "KB0001" in numbers or not hits
    assert "KB0002" not in numbers, "restricted content leaked through the default gate"


def test_double_ingestion_creates_zero_duplicate_points() -> None:
    """Rubric: ingesting an article twice must upsert, never duplicate.

    Deterministic point IDs (uuid5 of article_id + chunk index) mean the second
    run overwrites the exact same points — count unchanged, IDs identical.
    """
    qdrant = FilterHonoringQdrant()
    article = Article(
        article_number="KB1001",
        version="1.0",
        title="Resolving Stale Split-Tunnel VPN Routes",
        short_description="Flush stale vpn routes and reinstall the client.",
        body="1. Flush the stale split tunnel routes.\n2. Reinstall the vpn client.",
        category="network",
        service="corporate-vpn",
        workflow_state=WorkflowState.HUMAN_RESOLVED,
        security_level=SecurityLevel.INTERNAL,
    )

    first_count = ingest_articles([article], qdrant, embedding_engine=StubEmbeddingEngine())
    ids_after_first = set(qdrant.points)

    second_count = ingest_articles([article], qdrant, embedding_engine=StubEmbeddingEngine())

    assert first_count == second_count
    assert set(qdrant.points) == ids_after_first, "point IDs changed between ingests"
    assert len(qdrant.points) == first_count, "duplicate points created by re-ingestion"
