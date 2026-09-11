"""CLI script to seed the Qdrant knowledge base with article chunks.

Single idempotent command: ensures the collection exists, chunks the
corpus, embeds all chunks in one dual-vector pass, upserts with
deterministic point IDs, and verifies the stored point count matches the
upserted count — exiting non-zero on any mismatch.
"""

import argparse
import logging
import sys
from pathlib import Path

from qdrant_client import QdrantClient

from app.core.config import get_retrieval_settings
from app.retrieval.embedding import FastEmbedEngine
from app.retrieval.ingest import ingest_articles
from app.retrieval.sources import LocalJSONSource

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed_qdrant")

DEFAULT_CORPUS = Path("data/corpus/barq_articles.json")


def main() -> int:
    settings = get_retrieval_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS,
        help=f"Path to the articles JSON array (default: {DEFAULT_CORPUS})",
    )
    parser.add_argument("--url", default=settings.qdrant_url, help="Qdrant endpoint URL")
    parser.add_argument(
        "--collection",
        default=settings.qdrant_collection_name,
        help="Collection name to seed",
    )
    args = parser.parse_args()

    if not args.corpus.exists():
        print(
            f"Error: corpus file not found at {args.corpus}. Run scripts/extract_barq_kb.py first.",
            file=sys.stderr,
        )
        return 1

    client = QdrantClient(url=args.url)
    collection_name = args.collection

    logger.info("Loading articles from %s...", args.corpus)
    source = LocalJSONSource(args.corpus)
    articles = source.load_articles()
    logger.info("Loaded %d articles", len(articles))

    engine = FastEmbedEngine()
    total_points = ingest_articles(
        articles=articles,
        client=client,
        collection_name=collection_name,
        embedding_engine=engine,
        purge_unknown_articles=True,
    )

    stored = client.get_collection(collection_name=collection_name).points_count
    if stored != total_points:
        logger.error(
            "Seeding verification FAILED for '%s': stored %d points but upserted %d. "
            "If the collection holds points that cannot be reconciled "
            "(e.g. from a different corpus or schema), rebuild it with "
            "`uv run python scripts/setup_qdrant.py --force-recreate` and re-seed.",
            collection_name,
            stored,
            total_points,
        )
        return 1

    logger.info(
        "Seeding complete for '%s': %d points stored and verified (upserted: %d)",
        collection_name,
        stored,
        total_points,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
