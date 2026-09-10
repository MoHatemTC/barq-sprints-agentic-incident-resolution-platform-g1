"""CLI script to seed the Qdrant knowledge base with article chunks."""

import logging
from pathlib import Path

from qdrant_client import QdrantClient

from app.core.config import get_settings
from app.retrieval.ingest import ingest_articles, setup_qdrant_collection
from app.retrieval.sources import LocalJSONSource

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed_qdrant")


def main() -> None:
    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url)
    collection_name = settings.qdrant_collection_name

    corpus_path = Path("data/corpus/barq_kb_articles.json")
    if not corpus_path.exists():
        corpus_path = Path("data/corpus/articles.json")

    logger.info("Loading articles from %s...", corpus_path)
    source = LocalJSONSource(corpus_path)
    articles = source.load_articles()
    logger.info("Loaded %d articles", len(articles))

    # Ensure collection exists
    setup_qdrant_collection(client, collection_name=collection_name, recreate=False)

    # Ingest articles
    total_points = ingest_articles(articles, client, collection_name=collection_name)
    info = client.get_collection(collection_name=collection_name)
    logger.info(
        "Seeding complete for '%s'. Points in collection: %d (upserted: %d)",
        collection_name,
        info.points_count,
        total_points,
    )


if __name__ == "__main__":
    main()
