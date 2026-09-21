"""CLI script to extract stressor articles (OCR pages, merged-cell tables,
and multi-column layouts) from the BARQ Operations Manual PDF and write
them to data/corpus/stressors/stressor_articles.json.

This is a separate, explicit step from seeding: extraction touches an OCR
engine and PDF geometry heuristics, both slower and more failure-prone than
JSON loading, so it runs once (or whenever the source PDF or
STRESSOR_REGISTRY changes) rather than on every seed. The resulting JSON is
reviewable and diffable like the rest of the corpus, and is loaded by
scripts/seed_qdrant.py through the same LocalJSONSource used for the
baseline articles.
"""

import argparse
import json
import sys
from pathlib import Path

import structlog

from app.core.config import Settings
from app.core.logging import configure_logging
from app.retrieval.extraction.stressor_articles import (
    STRESSOR_REGISTRY,
    build_stressor_articles,
)

settings = Settings()
configure_logging(environment=settings.environment, log_level=settings.log_level)
logger = structlog.get_logger("extract_stressors")

DEFAULT_PDF = Path("data/barq_manual.pdf")
DEFAULT_OUTPUT = Path("data/corpus/stressors/stressor_articles.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF, help="Source manual PDF")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Where to write the extracted articles JSON (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    if not args.pdf.exists():
        print(f"Error: source PDF not found at {args.pdf}", file=sys.stderr)
        return 1

    logger.info(
        "extracting_stressor_articles",
        pdf=str(args.pdf),
        registry_size=len(STRESSOR_REGISTRY),
    )
    articles = build_stressor_articles(args.pdf)

    if not articles:
        print(
            "Error: no stressor articles were extracted; check STRESSOR_REGISTRY page numbers "
            "against the actual PDF.",
            file=sys.stderr,
        )
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = [article.model_dump(mode="json") for article in articles]
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("stressor_articles_written", count=len(articles), output=str(args.output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
