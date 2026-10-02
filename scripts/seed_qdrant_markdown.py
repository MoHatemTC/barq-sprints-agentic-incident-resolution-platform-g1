"""CLI script to seed the Qdrant knowledge base with article chunks and Markdown manual chunks.

Single idempotent command: ensures the collection exists, chunks the corpus,
embeds and upserts deterministic points, then verifies each expected point's
identity and text. Unrelated live knowledge is preserved.
"""

import argparse
import logging
import sys
from pathlib import Path

import structlog
from qdrant_client import QdrantClient

from app.core.config import Settings, get_retrieval_settings
from app.core.logging import configure_logging
from app.retrieval.chunking import chunk_article
from app.retrieval.embedding import FastEmbedEngine
from app.retrieval.ingest import build_point_id, ingest_articles
from app.retrieval.manual.manual_chunking import chunk_sections
from app.retrieval.manual.manual_ingest import build_section_point_id, ingest_manual_sections
from app.retrieval.manual.manual_sources import MarkdownManualSource
from app.retrieval.sources import LocalJSONSource

settings = Settings()
configure_logging(environment=settings.environment, log_level=settings.log_level)

# stdlib logging still governs third-party library output (qdrant-client, httpx);
# our own messages go through structlog.
logging.basicConfig(level=logging.WARNING)
logger = structlog.get_logger("seed_qdrant_markdown")

DEFAULT_CORPUS = Path("data/corpus/barq_articles.json")
DEFAULT_STRESSORS = Path("data/corpus/stressors/stressor_articles.json")
DEFAULT_MARKDOWN = Path("data/corpus/barq_manual.md")


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
    parser.add_argument(
        "--markdown",
        type=Path,
        default=DEFAULT_MARKDOWN,
        help=(f"Path to the Structured.io parsed-pdf.md file (default: {DEFAULT_MARKDOWN})."),
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
        purge_unknown_articles=False,
    )

    if args.markdown.exists():
        logger.info("loading_manual_sections_from_markdown", corpus=str(args.markdown))
        manual_source = MarkdownManualSource(args.markdown)
        sections, relationships = manual_source.load_sections()
        logger.info("manual_sections_loaded", count=len(sections))

        chunks = chunk_sections(sections, relationships)
        logger.info("manual_chunks_created", count=len(chunks))

        manual_points = ingest_manual_sections(
            chunks=chunks,
            client=client,
            collection_name=collection_name,
            embedding_engine=engine,
            purge_unknown_sections=False,
        )
        total_points += manual_points
    else:
        logger.info("markdown_corpus_skipped", reason="file not found", path=str(args.markdown))

    expected = {
        build_point_id(article.article_id, chunk.chunk_index): (
            article.article_id,
            chunk.text,
        )
        for article in all_articles
        for chunk in chunk_article(article, chunk_size=700, chunk_overlap=120)
    }

    if args.markdown.exists():
        expected.update(
            {
                build_section_point_id(chunk.section_id, chunk.chunk_index): (
                    chunk.section_id,
                    chunk.text,
                )
                for chunk in chunks
            }
        )

    missing_or_changed = []
    for ids in (list(expected)[start : start + 100] for start in range(0, len(expected), 100)):
        found = {
            str(point.id): point.payload or {}
            for point in client.retrieve(
                collection_name=collection_name,
                ids=ids,
                with_payload=True,
                with_vectors=False,
            )
        }
        for point_id in ids:
            identity, body = expected[point_id]
            payload = found.get(point_id, {})
            if (
                payload.get("article_id", payload.get("section_id")) != identity
                or payload.get("chunk_text") != body
            ):
                missing_or_changed.append(point_id)

    stored = client.get_collection(collection_name=collection_name).points_count
    if missing_or_changed or len(expected) != total_points:
        logger.error(
            "seeding_verification_failed",
            collection=collection_name,
            stored=stored,
            expected=len(expected),
            missing_or_changed=len(missing_or_changed),
            remedy="re-run the seed; preserve unrelated live knowledge",
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
