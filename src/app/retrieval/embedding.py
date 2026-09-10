"""Dual embedding engine combining dense (bge-small-en-v1.5) and sparse (bm25) vectors."""

from __future__ import annotations

from collections.abc import Sequence

from fastembed import SparseTextEmbedding, TextEmbedding
from qdrant_client.models import SparseVector

DEFAULT_DENSE_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_SPARSE_MODEL = "Qdrant/bm25"
DENSE_VECTOR_SIZE = 384


class DualEmbeddingEngine:
    """Computes dense and sparse embeddings for hybrid retrieval in Qdrant.

    Important: FastEmbed's BM25 computes inverse document frequencies (IDF) across
    the batch passed to embed(). To avoid distorted IDF weights, embed_documents()
    should be called once on the complete set of corpus chunks, rather than
    per-article or per-chunk.
    """

    def __init__(
        self,
        dense_model_name: str = DEFAULT_DENSE_MODEL,
        sparse_model_name: str = DEFAULT_SPARSE_MODEL,
    ) -> None:
        self.dense_model_name = dense_model_name
        self.sparse_model_name = sparse_model_name
        self._dense_model: TextEmbedding | None = None
        self._sparse_model: SparseTextEmbedding | None = None

    @property
    def dense_model(self) -> TextEmbedding:
        if self._dense_model is None:
            self._dense_model = TextEmbedding(model_name=self.dense_model_name)
        return self._dense_model

    @property
    def sparse_model(self) -> SparseTextEmbedding:
        if self._sparse_model is None:
            self._sparse_model = SparseTextEmbedding(model_name=self.sparse_model_name)
        return self._sparse_model

    @property
    def dimension(self) -> int:
        return DENSE_VECTOR_SIZE

    def embed_documents(
        self, documents: Sequence[str]
    ) -> tuple[list[list[float]], list[SparseVector]]:
        """Embed a batch of document chunks with both dense and sparse representations.

        Args:
            documents: List of chunk text strings.

        Returns:
            Tuple of (dense_vectors, sparse_vectors).
        """
        if not documents:
            return [], []

        # Dense embeddings
        dense_gen = self.dense_model.embed(documents)
        dense_vectors = [v.tolist() for v in dense_gen]

        # Sparse BM25 embeddings (batch-fitted IDF across all documents in call)
        sparse_gen = self.sparse_model.embed(documents)
        sparse_vectors = [
            SparseVector(
                indices=s.indices.tolist(),
                values=s.values.tolist(),
            )
            for s in sparse_gen
        ]

        return dense_vectors, sparse_vectors

    def embed_query(self, query: str) -> tuple[list[float], SparseVector]:
        """Embed a single search query.

        Args:
            query: The user query or incident symptom text.

        Returns:
            Tuple of (dense_vector, sparse_vector).
        """
        # Dense query embedding
        dense_gen = iter(self.dense_model.query_embed(query))
        dense_vector = next(dense_gen).tolist()

        # Sparse BM25 query embedding
        sparse_gen = iter(self.sparse_model.query_embed(query))
        sparse_out = next(sparse_gen)
        sparse_vector = SparseVector(
            indices=sparse_out.indices.tolist(),
            values=sparse_out.values.tolist(),
        )

        return dense_vector, sparse_vector
