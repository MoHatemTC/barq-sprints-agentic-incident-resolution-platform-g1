"""CLI script to seed the Qdrant knowledge base with article chunks.

Single idempotent command: ensures the collection exists, chunks the
corpus (baseline + stressor articles, when present),
embeds all chunks in one dual-vector pass, upserts with
deterministic point IDs, and verifies the stored point count matches the
upserted count — exiting non-zero on any mismatch.
"""

import argparse
import logging
import sys
from pathlib import Path

import structlog
from qdrant_client import QdrantClient

from app.core.config import Settings, get_retrieval_settings
from app.core.logging import configure_logging
from app.retrieval.embedding import FastEmbedEngine
from app.retrieval.ingest import ingest_articles
from app.retrieval.sources import LocalJSONSource

settings = Settings()
configure_logging(environment=settings.environment, log_level=settings.log_level)

# stdlib logging still governs third-party library output (qdrant-client, httpx);
# our own messages go through structlog.
logging.basicConfig(level=logging.WARNING)
logger = structlog.get_logger("seed_qdrant")

DEFAULT_CORPUS = Path("data/corpus/barq_articles.json")
DEFAULT_STRESSORS = Path("data/corpus/stressors/stressor_articles.json")


def main() -> int:
    settings = get_retrieval_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS,
        help=f"Path to the articles JSON array (default: {DEFAULT_CORPUS})",
    )
    parser.add_argument(
        "--stressors",
        type=Path,
        default=DEFAULT_STRESSORS,
        help=(
            "Path to the stressor articles JSON array produced by "
            f"scripts/extract_stressors.py (default: {DEFAULT_STRESSORS}). "
            "Silently skipped if the file does not exist."
        ),
    )
    parser.add_argument(
        "--no-stressors",
        action="store_true",
        help="Seed only the baseline corpus, ignoring --stressors entirely.",
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

    # Stressor articles are merged into the same list before chunking
    all_articles = list(articles)
    if not args.no_stressors:
        if args.stressors.exists():
            logger.info("loading_stressor_articles", corpus=str(args.stressors))
            stressor_source = LocalJSONSource(args.stressors)
            stressor_articles = stressor_source.load_articles()
            logger.info("stressor_articles_loaded", count=len(stressor_articles))
            all_articles.extend(stressor_articles)
        else:
            logger.info(
                "stressor_articles_skipped",
                reason="file not found",
                path=str(args.stressors),
                remedy="run scripts/extract_stressors.py first",
            )

    engine = FastEmbedEngine()
    total_points = ingest_articles(
        articles=all_articles,
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
