"""Agent bundling must preserve cross-encoder rank while keeping cosine gates."""

from unittest.mock import MagicMock, patch

import pytest

from agent.retrieval import QdrantRetriever
from app.core.config import RetrievalMode, RetrievalSettings
from app.models.knowledge import Classification
from tests.agent_support import evidence


@pytest.mark.parametrize("label", [Classification.NETWORK, Classification.OTHER])
@pytest.mark.parametrize("mode", [RetrievalMode.HYBRID, RetrievalMode.HYBRID_RERANKED])
def test_final_agent_order_respects_selected_mode_without_changing_evidence_gate(
    label: Classification, mode: RetrievalMode
) -> None:
    dense_best = evidence("KB0001", relevance=0.7).model_copy(update={"fused_score": 0.1})
    rerank_best = evidence("KB0002", relevance=0.2).model_copy(update={"fused_score": 0.9})
    retriever = QdrantRetriever(MagicMock, MagicMock)
    with (
        patch(
            "agent.retrieval.get_retrieval_settings",
            return_value=RetrievalSettings(_env_file=None, retrieval_mode=mode),
        ),
        patch.object(retriever, "_one_pass", return_value=([rerank_best, dense_best], 0.7)),
    ):
        result = retriever.search("incident", classification=label, top_k=2, threshold=0.9)
    assert result.hits[0].article_number == (
        "KB0002" if mode == RetrievalMode.HYBRID_RERANKED else "KB0001"
    )
    assert result.best_relevance == 0.7
    # A reranker score of 0.9 cannot turn weak cosine evidence into a passing gate.
    assert result.sufficient is False


def test_reranked_article_keeps_complementary_resolution_and_exact_capacity() -> None:
    symptom = evidence("KB0002", section="Symptom", chunk=0, relevance=0.6).model_copy(
        update={"fused_score": 0.95}
    )
    resolution = evidence("KB0002", relevance=0.4).model_copy(update={"fused_score": 0.5})
    other = evidence("KB0001", relevance=0.8).model_copy(update={"fused_score": 0.2})
    retriever = QdrantRetriever(MagicMock, MagicMock)
    with (
        patch(
            "agent.retrieval.get_retrieval_settings",
            return_value=RetrievalSettings(
                _env_file=None, retrieval_mode=RetrievalMode.HYBRID_RERANKED
            ),
        ),
        patch.object(retriever, "_one_pass", return_value=([symptom, resolution, other], 0.8)),
    ):
        result = retriever.search(
            "incident", classification=Classification.NETWORK, top_k=2, threshold=0.55
        )
        single = retriever.search(
            "incident", classification=Classification.NETWORK, top_k=1, threshold=0.55
        )
    assert [(hit.article_number, hit.section) for hit in result.hits] == [
        ("KB0002", "Symptom"),
        ("KB0002", "Resolution"),
    ]
    assert result.sufficient is True
    assert len(single.hits) == 1
