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

import structlog
from qdrant_client import QdrantClient

from app.core.config import get_retrieval_settings
from app.retrieval.embedding import FastEmbedEngine
from app.retrieval.ingest import ingest_articles
from app.retrieval.sources import LocalJSONSource

# stdlib logging still governs third-party library output (qdrant-client, httpx);
# our own messages go through structlog.
logging.basicConfig(level=logging.WARNING)
logger = structlog.get_logger("seed_qdrant")

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

    logger.info("loading_articles", corpus=str(args.corpus))
    source = LocalJSONSource(args.corpus)
    articles = source.load_articles()
    logger.info("articles_loaded", count=len(articles))

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
            "seeding_verification_failed",
            collection=collection_name,
            stored=stored,
            upserted=total_points,
            remedy="rebuild with: uv run python scripts/setup_qdrant.py --force-recreate",
        )
        return 1

    logger.info(
        "seeding_complete",
        collection=collection_name,
        points=stored,
        upserted=total_points,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
