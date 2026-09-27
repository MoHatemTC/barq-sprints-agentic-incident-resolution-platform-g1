"""CLI script to seed the Qdrant knowledge base with article chunks.

Single idempotent command: ensures the collection exists, chunks the
corpus (baseline, plus the manifest-adapted manual with ``--with-manual-kb``,
plus legacy stressor articles when present), embeds all chunks in
one dual-vector pass, and upserts with deterministic point IDs. Seeding
never deletes records it was not given: articles outside the corpus (for
example human-captured KB articles) and non-article records in the
collection are left untouched. Completion is verified per expected point
ID and chunk content — not by whole-collection counts — exiting non-zero
on any mismatch.

Raw manual sections (``--manual-corpus``) are an opt-in scratch path: they
are chunked and ingested only into the separate ``--manual-collection``
target given alongside, never into the article collection and never as a
silent side effect.
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
from app.retrieval.manual.integration import load_manual_publication
from app.retrieval.manual.manual_chunking import chunk_sections
from app.retrieval.manual.manual_ingest import ingest_manual_sections
from app.retrieval.manual.manual_sources import ManualCorpusJSONSource
from app.retrieval.sources import LocalJSONSource
from app.retrieval.verification import SeedVerificationError, verify_seeded_articles

settings = Settings()
configure_logging(environment=settings.environment, log_level=settings.log_level)

# stdlib logging still governs third-party library output (qdrant-client, httpx);
# our own messages go through structlog.
logging.basicConfig(level=logging.WARNING)
logger = structlog.get_logger("seed_qdrant")

DEFAULT_CORPUS = Path("data/corpus/barq_articles.json")
DEFAULT_STRESSORS = Path("data/corpus/stressors/stressor_articles.json")
MANIFEST_PATH = Path("data/corpus/manual_kb_manifest.json")
MANUAL_SECTIONS_PATH = Path("data/corpus/manual_sections.json")


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
        "--with-manual-kb",
        action="store_true",
        help=(
            "Adapt the manual through the reviewed manifest and ingest the KB2xxx "
            "publication units into the article collection alongside the corpus."
        ),
    )
    parser.add_argument(
        "--manual-corpus",
        type=Path,
        default=None,
        help=(
            "Opt-in path to a raw manual sections JSON (scratch format). "
            "Requires --manual-collection; never ingested by default."
        ),
    )
    parser.add_argument(
        "--manual-collection",
        default=None,
        help=(
            "Separate scratch target collection for --manual-corpus. Must "
            "differ from the article collection: co-location is rejected."
        ),
    )
    parser.add_argument("--url", default=settings.qdrant_url, help="Qdrant endpoint URL")
    parser.add_argument(
        "--collection",
        default=settings.qdrant_collection_name,
        help="Collection name to seed",
    )
    args = parser.parse_args()

    if (args.manual_corpus is None) != (args.manual_collection is None):
        print(
            "Error: --manual-corpus and --manual-collection must be given together.",
            file=sys.stderr,
        )
        return 1
    if args.manual_corpus is not None:
        if args.manual_collection == args.collection:
            logger.error(
                "manual_collection_rejected",
                reason="co-location with the article collection is not allowed",
                article_collection=args.collection,
                manual_collection=args.manual_collection,
            )
            return 1
        if not args.manual_corpus.exists():
            print(
                f"Error: manual corpus file not found at {args.manual_corpus}.",
                file=sys.stderr,
            )
            return 1

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
                remedy=(
                    "legacy stressor records are retired via the reconciliation plan; "
                    "do not re-extract"
                ),
            )

    engine = FastEmbedEngine()
    article_provenance = None
    if args.with_manual_kb:
        logger.info("adapting_manual_kb", manifest=str(MANIFEST_PATH))
        publication = load_manual_publication(
            manifest_path=MANIFEST_PATH,
            sections_path=MANUAL_SECTIONS_PATH,
            corpus_path=args.corpus,
        )
        logger.info("manual_kb_adapted", publication_units=len(publication.articles))
        all_articles.extend(publication.articles)
        article_provenance = publication.provenance

    ingest_articles(
        articles=all_articles,
        client=client,
        collection_name=collection_name,
        embedding_engine=engine,
        article_provenance=article_provenance,
    )

    if args.manual_corpus is not None:
        logger.info("loading_manual_sections", corpus=str(args.manual_corpus))
        manual_source = ManualCorpusJSONSource(args.manual_corpus)
        sections, relationships = manual_source.load_sections()
        logger.info("manual_sections_loaded", count=len(sections))

        chunks = chunk_sections(sections, relationships)
        logger.info("manual_sections_chunked", chunks=len(chunks))

        manual_points = ingest_manual_sections(
            chunks=chunks,
            client=client,
            collection_name=args.manual_collection,
            embedding_engine=engine,
        )
        logger.info(
            "manual_sections_ingested",
            points=manual_points,
            collection=args.manual_collection,
        )

    try:
        verify_seeded_articles(client, collection_name, all_articles)
    except SeedVerificationError as err:
        logger.error(
            "seeding_verification_failed",
            collection=collection_name,
            error=str(err),
            remedy="inspect the collection; do NOT force-recreate — it would delete live knowledge",
        )
        return 1

    logger.info(
        "seeding_complete",
        collection=collection_name,
        articles=len(all_articles),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
