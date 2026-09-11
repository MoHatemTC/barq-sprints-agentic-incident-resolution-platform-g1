"""Tests for the dual dense/sparse FastEmbed engine and Qdrant collection setup."""

import pytest
from pydantic import ValidationError
from qdrant_client import QdrantClient

from app.clients.qdrant import (
    DENSE_VECTOR_NAME,
    DENSE_VECTOR_SIZE,
    SPARSE_VECTOR_NAME,
    ensure_collection,
    get_qdrant_client,
)
from app.core.config import get_retrieval_settings
from app.retrieval.embedding import EmbeddedText, EmbeddingEngine, FastEmbedEngine


def test_embedded_text_is_frozen_and_valid() -> None:
    embed = EmbeddedText(
        dense=[0.1, 0.2, 0.3],
        sparse_indices=[10, 20],
        sparse_values=[1.5, 2.5],
    )
    assert embed.dense == [0.1, 0.2, 0.3]
    assert embed.sparse_indices == [10, 20]
    assert embed.sparse_values == [1.5, 2.5]

    with pytest.raises(ValidationError):
        # Must be immutable / frozen
        embed.dense = [0.4, 0.5]  # type: ignore[misc]


def test_fastembed_engine_implements_protocol() -> None:
    engine = FastEmbedEngine()
    assert isinstance(engine, EmbeddingEngine)


def test_fastembed_dense_vector_shape_and_sparse_properties() -> None:
    engine = FastEmbedEngine()
    texts = ["PostgreSQL 16 connection limit exceeded under connection pooling."]
    results = engine.embed_documents(texts)

    assert len(results) == 1
    embedded = results[0]

    # Dense vector shape: 384 dimensions (bge-small-en-v1.5)
    assert len(embedded.dense) == DENSE_VECTOR_SIZE

    # Sparse vector properties (BM25)
    assert len(embedded.sparse_indices) == len(embedded.sparse_values)
    assert len(embedded.sparse_indices) > 0
    assert all(isinstance(idx, int) for idx in embedded.sparse_indices)
    assert all(val >= 0.0 for val in embedded.sparse_values)


def test_fastembed_embed_query_path() -> None:
    engine = FastEmbedEngine()
    query = "VPN authentication failure after a password reset"
    result = engine.embed_query(query)

    assert len(result.dense) == DENSE_VECTOR_SIZE
    assert len(result.sparse_indices) == len(result.sparse_values)
    assert len(result.sparse_indices) > 0


def test_fastembed_batch_order_preservation() -> None:
    engine = FastEmbedEngine()
    texts = [
        "First document about VPN network authentication failure.",
        "Second document about printer queue hardware jam.",
        "Third document about SAP ERP RFC timeout.",
    ]
    results = engine.embed_documents(texts)

    assert len(results) == 3
    # Different documents should have distinct embeddings
    assert results[0].dense != results[1].dense
    assert results[1].dense != results[2].dense
    assert results[0].sparse_indices != results[1].sparse_indices


def test_fastembed_determinism() -> None:
    engine = FastEmbedEngine()
    text = "Order service database connection pool exhausted returning HTTP 500."

    run1 = engine.embed_documents([text])[0]
    run2 = engine.embed_documents([text])[0]

    assert run1.dense == pytest.approx(run2.dense, abs=1e-5)
    assert run1.sparse_indices == run2.sparse_indices
    assert run1.sparse_values == pytest.approx(run2.sparse_values, abs=1e-5)


def test_retrieval_settings_independent_of_servicenow() -> None:
    settings = get_retrieval_settings()
    assert settings.qdrant_url
    assert settings.dense_embedding_model == "BAAI/bge-small-en-v1.5"
    assert settings.sparse_embedding_model == "Qdrant/bm25"
    assert settings.qdrant_collection_name == "incident_knowledge_base"


def test_qdrant_client_factory() -> None:
    client = get_qdrant_client("http://localhost:6333")
    assert isinstance(client, QdrantClient)


def test_qdrant_ensure_collection_in_memory() -> None:
    client = QdrantClient(":memory:")
    collection_name = "test_incident_kb"

    ensure_collection(client, collection_name)
    assert client.collection_exists(collection_name)

    info = client.get_collection(collection_name)
    vectors_config = info.config.params.vectors
    assert isinstance(vectors_config, dict)
    assert DENSE_VECTOR_NAME in vectors_config
    assert vectors_config[DENSE_VECTOR_NAME].size == DENSE_VECTOR_SIZE

    sparse_config = info.config.params.sparse_vectors
    assert isinstance(sparse_config, dict)
    assert SPARSE_VECTOR_NAME in sparse_config
    assert sparse_config[SPARSE_VECTOR_NAME].modifier is None

    # Idempotent call
    ensure_collection(client, collection_name, force_recreate=False)
    assert client.collection_exists(collection_name)

    # Force recreate call
    ensure_collection(client, collection_name, force_recreate=True)
    assert client.collection_exists(collection_name)


def test_engine_reports_real_dense_vector_size() -> None:
    """bge-small-en-v1.5 produces 384d — the engine must surface its model's true dimension."""
    engine = FastEmbedEngine()
    assert engine.dense_vector_size == 384


def test_ensure_collection_derives_size_from_engine() -> None:
    """A swapped embedding model re-configures fresh collections at its own dimension."""
    from unittest.mock import MagicMock

    client = QdrantClient(":memory:")
    engine = MagicMock()
    engine.dense_vector_size = 768
    ensure_collection(client, "derived_kb", dense_vector_size=engine.dense_vector_size)

    info = client.get_collection("derived_kb")
    vectors_config = info.config.params.vectors
    assert isinstance(vectors_config, dict)
    assert vectors_config[DENSE_VECTOR_NAME].size == 768


def test_dimension_mismatch_raises_with_remedy() -> None:
    """A 384d collection attached to a 768d model must fail at setup, not at upsert."""
    from unittest.mock import MagicMock

    client = QdrantClient(":memory:")
    ensure_collection(client, "mismatch_kb")  # default 384d

    engine = MagicMock()
    engine.dense_vector_size = 768
    with pytest.raises(ValueError, match="768d vectors"):
        ensure_collection(client, "mismatch_kb", dense_vector_size=engine.dense_vector_size)

    # The remedy path works: force-recreate rebuilds at the engine's dimension.
    ensure_collection(
        client, "mismatch_kb", dense_vector_size=engine.dense_vector_size, force_recreate=True
    )
    info = client.get_collection("mismatch_kb")
    vectors_config = info.config.params.vectors
    assert isinstance(vectors_config, dict)
    assert vectors_config[DENSE_VECTOR_NAME].size == 768
