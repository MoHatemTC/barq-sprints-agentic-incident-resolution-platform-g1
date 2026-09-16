"""Tests for the Qdrant client factory and collection setup (dimension guard included)."""

from unittest.mock import MagicMock

import pytest
from qdrant_client import QdrantClient, models

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
    # #44: fastembed's Bm25 sets requires_idf=True and does not fit IDF client-side,
    # so Qdrant must apply it or every query token weighs 1.0.
    assert sparse_config[SPARSE_VECTOR_NAME].modifier == models.Modifier.IDF

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


def test_ensure_collection_rejects_a_collection_without_the_idf_modifier() -> None:
    """A pre-#44 collection has modifier=None and must not be reused silently.

    BM25 without IDF degrades scoring rather than erroring, so the mismatch has to be
    caught the same way a wrong dense dimension already is.
    """
    client = QdrantClient(":memory:")
    name = "legacy_collection_without_idf"

    client.create_collection(
        collection_name=name,
        vectors_config={
            DENSE_VECTOR_NAME: models.VectorParams(
                size=DENSE_VECTOR_SIZE,
                distance=models.Distance.COSINE,
            ),
        },
        sparse_vectors_config={
            SPARSE_VECTOR_NAME: models.SparseVectorParams(
                index=models.SparseIndexParams(on_disk=False),
                modifier=None,
            ),
        },
    )

    with pytest.raises(ValueError, match="sparse modifier"):
        ensure_collection(client, name)

    # The remedy is named in the message, and it works.
    ensure_collection(client, name, force_recreate=True)
    info = client.get_collection(name)
    sparse_config = info.config.params.sparse_vectors
    assert isinstance(sparse_config, dict)
    assert sparse_config[SPARSE_VECTOR_NAME].modifier == models.Modifier.IDF
