"""Script to extract sections from parsed-pdf.md and write them to JSON.

This script parses the raw markdown using the new Markdown pipeline and exports
the resulting sections and relationships to a JSON corpus file (usually
manual_sections.json). This allows us to inspect the fully parsed output and
maintain compatibility with older tools that expect the JSON format.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import structlog

from app.core.config import Settings
from app.core.logging import configure_logging
from app.retrieval.manual.manual_sources import (
    sections_and_relationships_to_json,
)
from app.retrieval.manual.markdown_parser import parse_markdown_manual

settings = Settings()
configure_logging(environment=settings.environment, log_level=settings.log_level)

logging.basicConfig(level=logging.WARNING)
logger = structlog.get_logger("extract_markdown_to_json")

DEFAULT_MARKDOWN = Path("data/corpus/barq_manual.md")
DEFAULT_OUTPUT = Path("data/corpus/manual_markdown_sections.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--markdown",
        type=Path,
        default=DEFAULT_MARKDOWN,
        help=f"Path to the parsed-pdf.md file (default: {DEFAULT_MARKDOWN})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output JSON file path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    if not args.markdown.exists():
        logger.error(
            "markdown_file_not_found",
            path=str(args.markdown),
            remedy="Ensure the parsed-pdf.md file is in the correct location.",
        )
        return 1

    logger.info("parsing_markdown", markdown=str(args.markdown))

    # We use parse_markdown_manual directly to also get the parse report
    sections, relationships, report = parse_markdown_manual(args.markdown)

    logger.info(
        "markdown_parsed",
        total_sections=report.total_sections,
        tables_converted=report.tables_converted,
        warnings=len(report.warnings),
    )

    payload = sections_and_relationships_to_json(sections, relationships)

    logger.info("writing_json", output=str(args.output))
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    logger.info(
        "json_extraction_complete", output_path=str(args.output), sections_written=len(sections)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
