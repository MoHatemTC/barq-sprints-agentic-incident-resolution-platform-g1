"""Embedding engine protocol and FastEmbed implementation for dual dense/sparse vectors."""

from typing import Protocol, runtime_checkable

from fastembed import SparseTextEmbedding, TextEmbedding
from pydantic import BaseModel, ConfigDict

from app.core.config import get_retrieval_settings


class EmbeddedText(BaseModel):
    """Frozen value object holding paired dense and sparse vectors for a single text chunk."""

    model_config = ConfigDict(frozen=True)

    dense: list[float]
    sparse_indices: list[int]
    sparse_values: list[float]


@runtime_checkable
class EmbeddingEngine(Protocol):
    """Protocol defining the embedding boundary (NFR-09 swap boundary)."""

    @property
    def dense_vector_size(self) -> int:
        """Output dimension of the dense vectors this engine produces."""
        ...

    def embed_documents(self, texts: list[str]) -> list[EmbeddedText]:
        """Embed a batch of document texts in a single pass over the corpus."""
        ...

    def embed_query(self, text: str) -> EmbeddedText:
        """Embed a single search query text."""
        ...


DENSE_VECTOR_SIZE = 384


class FastEmbedEngine:
    """FastEmbed-backed dual embedding engine using bge-small-en-v1.5 and Qdrant/bm25."""

    def __init__(
        self,
        dense_model: str | None = None,
        sparse_model: str | None = None,
    ) -> None:
        settings = get_retrieval_settings()
        self.dense_model_name = dense_model or settings.dense_embedding_model
        self.sparse_model_name = sparse_model or settings.sparse_embedding_model
        self._dense_model = TextEmbedding(model_name=self.dense_model_name)
        self._sparse_model = SparseTextEmbedding(model_name=self.sparse_model_name)

    @property
    def dense_vector_size(self) -> int:
        """Output dimension of the configured dense model (e.g. 384 for bge-small)."""
        return int(self._dense_model.embedding_size)

    def embed_documents(self, texts: list[str]) -> list[EmbeddedText]:
        """Embed document texts with exactly ONE .embed() call per model over the full batch.

        This guarantees that BM25 IDF weights are properly fitted across the corpus,
        and zip(..., strict=True) guards chunk-to-vector alignment.
        """
        if not texts:
            return []

        dense_vectors = self._dense_model.embed(texts)
        sparse_vectors = self._sparse_model.embed(texts)

        results: list[EmbeddedText] = []
        for dense_vec, sparse_vec in zip(dense_vectors, sparse_vectors, strict=True):
            dense_list = dense_vec.tolist() if hasattr(dense_vec, "tolist") else list(dense_vec)
            sparse_indices = (
                sparse_vec.indices.tolist()
                if hasattr(sparse_vec.indices, "tolist")
                else list(sparse_vec.indices)
            )
            sparse_values = (
                sparse_vec.values.tolist()
                if hasattr(sparse_vec.values, "tolist")
                else [float(v) for v in sparse_vec.values]
            )

            results.append(
                EmbeddedText(
                    dense=dense_list,
                    sparse_indices=sparse_indices,
                    sparse_values=[float(v) for v in sparse_values],
                )
            )

        return results

    def embed_query(self, text: str) -> EmbeddedText:
        """Embed a query text using .query_embed() for asymmetric retrieval compatibility."""
        dense_vec = next(iter(self._dense_model.query_embed(text)))
        sparse_vec = next(iter(self._sparse_model.query_embed(text)))

        dense_list = dense_vec.tolist() if hasattr(dense_vec, "tolist") else list(dense_vec)
        sparse_indices = (
            sparse_vec.indices.tolist()
            if hasattr(sparse_vec.indices, "tolist")
            else list(sparse_vec.indices)
        )
        sparse_values = (
            sparse_vec.values.tolist()
            if hasattr(sparse_vec.values, "tolist")
            else [float(v) for v in sparse_vec.values]
        )

        return EmbeddedText(
            dense=dense_list,
            sparse_indices=sparse_indices,
            sparse_values=[float(v) for v in sparse_values],
        )
