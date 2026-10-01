"""Diversity must improve coverage without separating evidence companions."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from agent.diversity import mmr_order
from agent.retrieval import QdrantRetriever
from app.clients.qdrant import DENSE_VECTOR_NAME
from app.core.config import RetrievalMode, RetrievalSettings
from app.models.knowledge import Classification
from app.workers.retry_policy import RetryableError, TerminalError
from tests.agent_support import evidence


def test_mmr_replaces_redundant_second_article_with_relevant_independent_fault() -> None:
    scores = {"vpn": 0.95, "vpn_duplicate": 0.94, "outlook": 0.9}
    vectors = {"vpn": [1, 0], "vpn_duplicate": [1, 0], "outlook": [0, 1]}
    assert mmr_order(scores, vectors, lambda_mult=1) == ["vpn", "vpn_duplicate", "outlook"]
    assert mmr_order(scores, vectors, lambda_mult=0.8) == ["vpn", "outlook", "vpn_duplicate"]


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan")])
def test_invalid_lambda_is_rejected(value: float) -> None:
    with pytest.raises(ValueError):
        mmr_order({}, {}, lambda_mult=value)
    with pytest.raises(ValidationError):
        RetrievalSettings(_env_file=None, retrieval_mmr_lambda=value)


@pytest.mark.parametrize("bad", [[0, 0], [float("nan"), 0], [1, 0, 0]])
def test_invalid_vectors_do_not_silently_disable_mmr(bad: list[float]) -> None:
    with pytest.raises(ValueError):
        mmr_order({"a": 0.9, "b": 0.8}, {"a": [1, 0], "b": bad}, lambda_mult=0.8)


def test_ties_are_independent_of_backend_response_order() -> None:
    assert mmr_order({"b": 0.8, "a": 0.8}, {"b": [1, 0], "a": [0, 1]}, lambda_mult=0.5) == [
        "a",
        "b",
    ]


@pytest.mark.parametrize("classification", [Classification.NETWORK, Classification.OTHER])
def test_agent_diversifies_articles_and_retains_resolution_companions(classification) -> None:
    chunks = []
    for name, rank in [("KB0001", 0.95), ("KB0002", 0.94), ("KB0003", 0.9)]:
        chunks.extend(
            [
                evidence(name, section="Cause", chunk=0, relevance=0.8).model_copy(
                    update={"fused_score": rank}
                ),
                evidence(name, section="Resolution", chunk=1, relevance=0.75).model_copy(
                    update={"fused_score": rank - 0.05}
                ),
            ]
        )
    retriever = QdrantRetriever(MagicMock, MagicMock)
    vectors = {chunks[i].article_id: v for i, v in [(0, [1, 0]), (2, [1, 0]), (4, [0, 1])]}
    settings = RetrievalSettings(
        _env_file=None, retrieval_mode=RetrievalMode.HYBRID_RERANKED, retrieval_mmr_enabled=True
    )
    with (
        patch("agent.retrieval.get_retrieval_settings", return_value=settings),
        patch.object(retriever, "_one_pass", return_value=(chunks, 0.8)),
        patch.object(retriever, "_representative_vectors", return_value=vectors),
    ):
        result = retriever.search(
            "VPN and Outlook", classification=classification, top_k=4, threshold=0.55
        )
    assert [(x.article_number, x.section) for x in result.hits] == [
        ("KB0001", "Cause"),
        ("KB0001", "Resolution"),
        ("KB0003", "Cause"),
        ("KB0003", "Resolution"),
    ]
    assert result.sufficient
    assert result.mmr_applied
    assert result.mmr_lambda == 0.8
    assert result.best_relevance == 0.8


def test_diversity_cannot_promote_below_gate_evidence_or_change_first_match() -> None:
    chunks = [
        evidence("KB0001", section="Resolution", chunk=0, relevance=0.8).model_copy(
            update={"fused_score": 0.95}
        ),
        evidence("KB0002", section="Resolution", chunk=0, relevance=0.1).model_copy(
            update={"fused_score": 0.94}
        ),
        evidence("KB0003", section="Resolution", chunk=0, relevance=0.8).model_copy(
            update={"fused_score": 0.9}
        ),
    ]
    retriever = QdrantRetriever(MagicMock, MagicMock)
    settings = RetrievalSettings(
        _env_file=None, retrieval_mode=RetrievalMode.HYBRID_RERANKED, retrieval_mmr_enabled=True
    )
    with (
        patch("agent.retrieval.get_retrieval_settings", return_value=settings),
        patch.object(retriever, "_one_pass", return_value=(chunks, 0.8)),
        patch.object(
            retriever,
            "_representative_vectors",
            return_value={chunks[0].article_id: [1, 0], chunks[2].article_id: [0, 1]},
        ) as fetch,
    ):
        result = retriever.search(
            "incident", classification=Classification.NETWORK, top_k=3, threshold=0.55
        )
    assert [x.article_number for x in result.hits] == ["KB0001", "KB0002", "KB0003"]
    assert chunks[1].article_id not in fetch.call_args.args[1]


@pytest.mark.parametrize("classification", [Classification.NETWORK, Classification.OTHER])
@pytest.mark.parametrize("enabled,weight", [(False, 0.8), (True, 1.0)])
def test_disabled_and_relevance_only_modes_do_not_read_diversity_vectors(
    enabled, weight, classification
) -> None:
    chunks = [evidence("KB0001"), evidence("KB0002")]
    retriever = QdrantRetriever(MagicMock, MagicMock)
    settings = RetrievalSettings(
        _env_file=None, retrieval_mmr_enabled=enabled, retrieval_mmr_lambda=weight
    )
    with (
        patch("agent.retrieval.get_retrieval_settings", return_value=settings),
        patch.object(retriever, "_one_pass", return_value=(chunks, 0.8)),
        patch.object(retriever, "_representative_vectors") as fetch,
    ):
        retriever.search("incident", classification=classification, top_k=2, threshold=0.55)
    fetch.assert_not_called()


def test_vector_read_is_exact_version_chunk_and_preserves_security_filter() -> None:
    client = QdrantClient(":memory:")
    client.create_collection(
        "mmr", vectors_config={DENSE_VECTOR_NAME: VectorParams(size=2, distance=Distance.COSINE)}
    )
    chunk = evidence("KB0001", chunk=0)
    base = dict(
        article_number=chunk.article_number,
        version=chunk.version,
        chunk_index=0,
        workflow_state="published",
        security_level="internal",
    )
    client.upsert(
        "mmr",
        [
            PointStruct(
                id=str(uuid4()), vector={DENSE_VECTOR_NAME: vector}, payload={**base, **updates}
            )
            for vector, updates in [
                ([1, 0], {}),
                ([0, 1], {"version": "999"}),
                ([0, 1], {"chunk_index": 1}),
                ([0, 1], {"security_level": "restricted"}),
                ([0, 1], {"workflow_state": "draft"}),
            ]
        ],
    )
    retriever = QdrantRetriever(lambda: client, MagicMock, collection_name="mmr")
    try:
        assert retriever._representative_vectors(client, {chunk.article_id: chunk}) == {
            chunk.article_id: [1.0, 0.0]
        }
        missing = chunk.model_copy(update={"version": "missing"})
        with pytest.raises(TerminalError):
            retriever._representative_vectors(client, {missing.article_id: missing})
    finally:
        client.close()


def test_vector_transport_outage_is_retryable_not_masked() -> None:
    client = MagicMock()
    client.scroll.side_effect = TimeoutError
    chunk = evidence("KB0001")
    retriever = QdrantRetriever(lambda: client, MagicMock)
    with pytest.raises(RetryableError, match="MMR vectors unavailable"):
        retriever._representative_vectors(client, {chunk.article_id: chunk})
