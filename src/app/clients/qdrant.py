from collections.abc import Iterable

from qdrant_client import QdrantClient, models

from app.core.config import get_retrieval_settings

DENSE_VECTOR_SIZE = 384
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"

PAYLOAD_KEYWORD_INDEXES = [
    "category",
    "service",
    "workflow_state",
    "version",
    "security_level",
    "article_id",
    "article_number",
]

MANUAL_PAYLOAD_KEYWORD_INDEXES: tuple[str, ...] = (
    "section_number",
    "section_id",
    "content_type",
    "doc_type",
    "related_article_ids",
    "related_incident_ids",
    "related_problem_ids",
    "related_known_error_ids",
    "related_change_ids",
    "related_mir_ids",
)


def get_qdrant_client(
    url: str | None = None,
    api_key: str | None = None,
) -> QdrantClient:
    """Create a Qdrant client from provided parameters or retrieval settings."""
    settings = get_retrieval_settings()
    endpoint = url or settings.qdrant_url
    return QdrantClient(url=endpoint, api_key=api_key)


def _ensure_named_vector_collection(
    client: QdrantClient,
    name: str,
    *,
    dense_vector_size: int,
    force_recreate: bool,
    keyword_indexes: Iterable[str],
) -> None:
    """Shared setup for every collection in this pipeline."""
    exists = client.collection_exists(collection_name=name)

    if exists and force_recreate:
        client.delete_collection(collection_name=name)
        exists = False

    if exists:
        info = client.get_collection(collection_name=name)
        vectors = info.config.params.vectors
        existing = (
            vectors[DENSE_VECTOR_NAME]
            if isinstance(vectors, dict)
            else getattr(vectors, DENSE_VECTOR_NAME)
        )
        existing_size = existing.size
        if existing_size != dense_vector_size:
            raise ValueError(
                f"Collection '{name}' was created with dense dimension {existing_size}, "
                f"but the configured dense model produces {dense_vector_size}d vectors. "
                "Rebuild with `--force-recreate` (or the matching setup/seed script's "
                "equivalent flag) or configure a model matching the collection dimension."
            )

        # A collection created before #44 has modifier=None. BM25 scoring silently
        # degrades rather than erroring, so check it the same way as the dimension.
        sparse = info.config.params.sparse_vectors or {}
        existing_sparse = sparse.get(SPARSE_VECTOR_NAME)
        existing_modifier = getattr(existing_sparse, "modifier", None)
        if existing_modifier != models.Modifier.IDF:
            raise ValueError(
                f"Collection '{name}' has sparse modifier {existing_modifier!r}, "
                f"but BM25 requires {models.Modifier.IDF!r}. Without it, rare tokens "
                "score no higher than common ones. Rebuild with `--force-recreate`."
            )
    else:
        client.create_collection(
            collection_name=name,
            vectors_config={
                DENSE_VECTOR_NAME: models.VectorParams(
                    size=dense_vector_size,
                    distance=models.Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                SPARSE_VECTOR_NAME: models.SparseVectorParams(
                    index=models.SparseIndexParams(on_disk=False),
                    # fastembed 0.8.0's Bm25 sets requires_idf=True and warns that the
                    # model "is expected to be used with modifier='idf' in the sparse
                    # vector index of Qdrant". It does not fit IDF over the corpus
                    # client-side: every query token would otherwise weigh 1.0. See #44.
                    modifier=models.Modifier.IDF,
                ),
            },
        )

    # Ensure payload keyword indexes. create_payload_index is idempotent —
    # re-creating an existing index is a safe schema update — so failures here
    # are real (auth, network, permission) and must propagate, not be swallowed:
    # a collection silently missing its indexes breaks filtered search.
    for field_name in keyword_indexes:
        client.create_payload_index(
            collection_name=name,
            field_name=field_name,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )


def ensure_collection(
    client: QdrantClient,
    name: str,
    *,
    dense_vector_size: int = DENSE_VECTOR_SIZE,
    force_recreate: bool = False,
) -> None:
    _ensure_named_vector_collection(
        client,
        name,
        dense_vector_size=dense_vector_size,
        force_recreate=force_recreate,
        keyword_indexes=list(PAYLOAD_KEYWORD_INDEXES) + list(MANUAL_PAYLOAD_KEYWORD_INDEXES),
    )


def ensure_manual_collection(
    client: QdrantClient,
    name: str,
    *,
    dense_vector_size: int = DENSE_VECTOR_SIZE,
    force_recreate: bool = False,
) -> None:
    """Ensure the manual-sections collection exists."""
    _ensure_named_vector_collection(
        client,
        name,
        dense_vector_size=dense_vector_size,
        force_recreate=force_recreate,
        keyword_indexes=MANUAL_PAYLOAD_KEYWORD_INDEXES,
    )
