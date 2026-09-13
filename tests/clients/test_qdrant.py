"""Tests for the Qdrant client factory and collection setup (dimension guard included)."""

from unittest.mock import MagicMock

import pytest
from qdrant_client import QdrantClient

from app.clients.qdrant import (
    DENSE_VECTOR_NAME,
    DENSE_VECTOR_SIZE,
    SPARSE_VECTOR_NAME,
    ensure_collection,
    get_qdrant_client,
)


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


def test_ensure_collection_derives_size_from_engine() -> None:
    """A swapped embedding model re-configures fresh collections at its own dimension."""
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
