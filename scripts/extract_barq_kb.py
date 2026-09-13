#!/usr/bin/env python3
"""CLI script to extract knowledge base articles from the BARQ Operations Manual PDF."""

import argparse
import json
import sys
from pathlib import Path

from app.retrieval.barq_manual import extract_barq_manual_articles


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract knowledge articles from BARQ Operations Manual PDF into canonical JSON."
        )
    )

    parser.add_argument(
        "--pdf",
        type=Path,
        default=Path("data/barq-system-kb.pdf"),
        help="Path to data/barq-system-kb.pdf",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/corpus/barq_articles.json"),
        help="Output path for canonical JSON array",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("data/corpus/barq_ingestion_report.md"),
        help="Output path for sanitized extraction report",
    )
    args = parser.parse_args()

    if not args.pdf.exists():
        print(f"Error: PDF not found at {args.pdf}", file=sys.stderr)
        return 1

    print(f"Extracting articles from {args.pdf}...")
    articles, report = extract_barq_manual_articles(args.pdf)

    # 1. Write JSON
    args.out.parent.mkdir(parents=True, exist_ok=True)
    serialized = [art.model_dump(mode="json", exclude_none=True) for art in articles]
    args.out.write_text(json.dumps(serialized, indent=2), encoding="utf-8")
    print(f"Wrote {len(articles)} articles to {args.out}")

    # 2. Write Sanitized Markdown Report (record counts, warnings, no full bodies)
    report_lines = [
        "# BARQ Operations Manual — Extraction & Ingestion Report",
        "",
        "## Summary Statistics",
        f"- **Total Records Extracted**: {report.total_records}",
        f"- **Published Articles**: {report.published_count}",
        f"- **Retired Articles**: {report.retired_count}",
        "",
        "## Extracted Article Keys",
    ]
    for key in report.parsed_articles:
        report_lines.append(f"- `{key}`")

    if report.warnings:
        report_lines.extend(["", "## Ingestion Warnings"])
        for warn in report.warnings:
            report_lines.append(f"- {warn}")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(f"Wrote extraction report to {args.report}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
