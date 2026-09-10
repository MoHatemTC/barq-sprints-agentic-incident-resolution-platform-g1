import logging

from qdrant_client import QdrantClient, models

from app.core.config import get_retrieval_settings

logger = logging.getLogger(__name__)

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


def get_qdrant_client(
    url: str | None = None,
    api_key: str | None = None,
) -> QdrantClient:
    """Create a Qdrant client from provided parameters or retrieval settings."""
    settings = get_retrieval_settings()
    endpoint = url or settings.qdrant_url
    return QdrantClient(url=endpoint, api_key=api_key)


def ensure_collection(
    client: QdrantClient,
    name: str,
    *,
    force_recreate: bool = False,
) -> None:
    """Ensure the target collection exists with named dense and sparse vectors and payload indexes.

    Configuration follows Qdrant Advisor recommendations:
    - Named dense vector: 384d (Cosine distance, bge-small-en-v1.5)
    - Named sparse vector: BM25 in-memory inverted index (on_disk=False, NO Modifier.IDF)
    - KEYWORD payload indexes on all 5 metadata fields + unique article identifiers.
    """
    exists = client.collection_exists(collection_name=name)

    if exists and force_recreate:
        client.delete_collection(collection_name=name)
        exists = False

    if not exists:
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
                    modifier=None,  # FastEmbed already applies client-side IDF
                ),
            },
        )

    # Ensure payload keyword indexes. create_payload_index is idempotent —
    # re-creating an existing index is a safe schema update — so failures here
    # are real (auth, network, permission) and must propagate, not be swallowed:
    # a collection silently missing its indexes breaks Sprint 2 filtered search.
    for field_name in PAYLOAD_KEYWORD_INDEXES:
        client.create_payload_index(
            collection_name=name,
            field_name=field_name,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
