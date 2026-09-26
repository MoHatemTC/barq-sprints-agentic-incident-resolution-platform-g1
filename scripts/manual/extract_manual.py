"""
Extracts the BARQ Operations Manual PDF into a corpus JSON file.
"""

import argparse
import json
import sys
from pathlib import Path

import structlog

from app.core.config import Settings
from app.core.logging import configure_logging
from app.retrieval.manual.manual_parser import parse_manual
from app.retrieval.manual.manual_sources import sections_and_relationships_to_json

settings = Settings()
configure_logging(environment=settings.environment, log_level=settings.log_level)
logger = structlog.get_logger("extract_manual")

DEFAULT_MANUAL_PDF = Path("data/barq_manual.pdf")
DEFAULT_OUTPUT = Path("data/corpus/manual_sections.json")


def _parse_page_set(raw: str) -> set[int]:
    return {int(p) for p in raw.split(",") if p.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pdf",
        type=Path,
        default=DEFAULT_MANUAL_PDF,
        help=f"Path to the manual PDF (default: {DEFAULT_MANUAL_PDF})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Where to write the corpus JSON (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--table-pages",
        type=str,
        default="",
        help='Comma-separated 1-indexed page numbers known to contain tables, e.g. "9,15,16"',
    )
    parser.add_argument(
        "--layout-pages",
        type=str,
        default="",
        help="Comma-separated "
        "1-indexed page numbers known to need multi-column reading-order reflow.",
    )
    args = parser.parse_args()

    if not args.pdf.exists():
        print(f"Error: manual PDF not found at {args.pdf}", file=sys.stderr)
        return 1

    logger.info("parsing_manual", pdf=str(args.pdf))
    sections, relationships, report = parse_manual(
        args.pdf,
        table_pages=_parse_page_set(args.table_pages),
        layout_pages=_parse_page_set(args.layout_pages),
    )
    for warning in report.warnings:
        logger.warning("manual_parse_warning", detail=warning)

    if not sections:
        print(
            "Error: parsing produced zero sections; refusing to write an empty corpus.",
            file=sys.stderr,
        )
        return 1

    payload = sections_and_relationships_to_json(sections, relationships)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info(
        "manual_extracted",
        output=str(args.output),
        pages=report.total_pages,
        sections=len(sections),
        ocr_pages=report.ocr_pages,
        table_pages=report.table_pages,
        layout_pages=report.layout_pages,
    )
    print(f"Wrote {len(sections)} sections ({report.total_pages} pages) to {args.output}")
    if relationships.forward:
        print(f"Resolved {len(relationships.forward)} Appendix E identifier(s) to sections.")
    else:
        print("No Appendix E relationships resolved (not found, or the appendix was empty).")
    if report.warnings:
        print(f"{len(report.warnings)} warning(s) during extraction — see the log above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
