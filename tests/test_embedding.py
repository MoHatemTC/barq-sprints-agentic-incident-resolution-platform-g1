"""Tests for DualEmbeddingEngine."""

import pytest
from qdrant_client.models import SparseVector

from app.retrieval.embedding import (
    DEFAULT_DENSE_MODEL,
    DEFAULT_SPARSE_MODEL,
    DENSE_VECTOR_SIZE,
    DualEmbeddingEngine,
)
from retrieval.embedding import DualEmbeddingEngine as ShimEngine


@pytest.fixture
def engine() -> DualEmbeddingEngine:
    return DualEmbeddingEngine()


def test_engine_initialization(engine: DualEmbeddingEngine) -> None:
    assert engine.dense_model_name == DEFAULT_DENSE_MODEL
    assert engine.sparse_model_name == DEFAULT_SPARSE_MODEL
    assert engine.dimension == DENSE_VECTOR_SIZE


def test_embed_documents(engine: DualEmbeddingEngine) -> None:
    docs = [
        "Clear cached VPN credentials after a password change to fix authentication failures.",
        "Restarting the print spooler service removes stalled orphaned print jobs.",
    ]
    dense_vecs, sparse_vecs = engine.embed_documents(docs)

    assert len(dense_vecs) == len(docs)
    assert len(sparse_vecs) == len(docs)

    for d in dense_vecs:
        assert len(d) == DENSE_VECTOR_SIZE
        assert all(isinstance(x, float) for x in d)

    for s in sparse_vecs:
        assert isinstance(s, SparseVector)
        assert len(s.indices) > 0
        assert len(s.indices) == len(s.values)


def test_embed_empty_documents(engine: DualEmbeddingEngine) -> None:
    dense_vecs, sparse_vecs = engine.embed_documents([])
    assert dense_vecs == []
    assert sparse_vecs == []


def test_embed_query(engine: DualEmbeddingEngine) -> None:
    query = "VPN client invalid credentials after password reset"
    dense_vec, sparse_vec = engine.embed_query(query)

    assert len(dense_vec) == DENSE_VECTOR_SIZE
    assert isinstance(sparse_vec, SparseVector)
    assert len(sparse_vec.indices) > 0
    assert len(sparse_vec.indices) == len(sparse_vec.values)


def test_shim_matches_engine() -> None:
    assert ShimEngine is DualEmbeddingEngine
