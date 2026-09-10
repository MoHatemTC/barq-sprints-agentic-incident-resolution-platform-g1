"""CLI script to initialize the Qdrant knowledge base collection.

Idempotent by default: re-running ensures the collection and its payload
indexes exist without touching stored points. Pass --force-recreate to
drop and rebuild the collection from scratch (destroys all points).
"""

import argparse
import logging
import sys

from qdrant_client import QdrantClient

from app.clients.qdrant import ensure_collection
from app.core.config import get_retrieval_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("setup_qdrant")


def main() -> int:
    settings = get_retrieval_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=settings.qdrant_url, help="Qdrant endpoint URL")
    parser.add_argument(
        "--collection",
        default=settings.qdrant_collection_name,
        help="Collection name to ensure",
    )
    parser.add_argument(
        "--force-recreate",
        action="store_true",
        help="Drop and rebuild the collection (DESTROYS all stored points)",
    )
    args = parser.parse_args()

    client = QdrantClient(url=args.url)
    logger.info("Connecting to Qdrant at %s...", args.url)
    if args.force_recreate:
        logger.warning("force-recreate requested: collection %s will be deleted", args.collection)

    ensure_collection(client, args.collection, force_recreate=args.force_recreate)
    info = client.get_collection(collection_name=args.collection)
    logger.info(
        "Collection '%s' ready. Status: %s, points: %d",
        args.collection,
        info.status,
        info.points_count,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
