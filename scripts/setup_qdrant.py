"""CLI script to initialize the Qdrant knowledge base collection."""

import logging

from qdrant_client import QdrantClient

from app.core.config import get_settings
from app.retrieval.ingest import setup_qdrant_collection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("setup_qdrant")


def main() -> None:
    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url)
    collection_name = settings.qdrant_collection_name
    logger.info("Connecting to Qdrant at %s...", settings.qdrant_url)
    setup_qdrant_collection(client, collection_name=collection_name, recreate=True)
    info = client.get_collection(collection_name=collection_name)
    logger.info("Collection '%s' ready. Status: %s", collection_name, info.status)


if __name__ == "__main__":
    main()
