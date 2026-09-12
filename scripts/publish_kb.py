"""CLI script to publish the canonical corpus into the ServiceNow KB.

Single idempotent command: validates the corpus, then for each article
looks the row up by our stable u_source_id stamp — creating it on a fresh
PDI, updating it in place on re-runs — and verifies every write by reading
the row back. Exits non-zero if any article fails.

Requires in .env: SERVICENOW_INSTANCE_URL, SERVICENOW_USERNAME,
SERVICENOW_PASSWORD, SERVICENOW_KB_ID (sys_id of the target Knowledge Base
created in the PDI) and the u_source_id String field present on the
kb_knowledge table.
"""

import argparse
import json
import sys
from pathlib import Path

import structlog

from app.core.config import get_settings
from app.publishing.payload import build_kb_payload
from app.publishing.servicenow_kb import (
    ServiceNowKBClient,
    ServiceNowKBError,
    publish_article,
)
from app.retrieval.sources import LocalJSONSource

logger = structlog.get_logger("publish_kb")

DEFAULT_CORPUS = Path("data/corpus/barq_articles.json")
DEFAULT_REPORT = Path("data/corpus/publish_report.json")


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS,
        help=f"Path to the articles JSON array (default: {DEFAULT_CORPUS})",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help=f"Path for the machine-readable publish report (default: {DEFAULT_REPORT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and validate payloads without any HTTP traffic.",
    )
    args = parser.parse_args()

    if not args.corpus.exists():
        print(
            f"Error: corpus file not found at {args.corpus}. Run scripts/extract_barq_kb.py first.",
            file=sys.stderr,
        )
        return 1

    if not settings.servicenow_kb_id:
        logger.error(
            "missing_kb_id",
            remedy="Create a Knowledge Base in your PDI and set SERVICENOW_KB_ID in .env.",
        )
        return 1

    logger.info("loading_articles", corpus=str(args.corpus))
    articles = LocalJSONSource(args.corpus).load_articles()
    if not articles:
        logger.error("empty_corpus", corpus=str(args.corpus))
        return 1
    logger.info("articles_loaded", count=len(articles))

    if args.dry_run:
        for article in articles:
            payload = build_kb_payload(article, settings.servicenow_kb_id)
            logger.info(
                "payload_validated",
                article_id=payload["u_source_id"],
                short_description=payload["short_description"],
                body_html_chars=len(payload["text"]),
            )
        logger.info("dry_run_complete", articles=len(articles), http_calls=0)
        _write_report(
            args.report,
            {"dry_run": True, "created": 0, "updated": 0, "failed": [], "results": []},
        )
        return 0

    client = ServiceNowKBClient(
        instance_url=settings.servicenow_instance_url,
        username=settings.servicenow_username,
        password=settings.servicenow_password.get_secret_value(),
        timeout_seconds=settings.servicenow_timeout_seconds,
    )

    results: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    try:
        unique_categories = sorted({a.category for a in articles if a.category})
        category_mapping = client.run_preflight(settings.servicenow_kb_id, unique_categories)

        for article in articles:
            try:
                outcome = publish_article(
                    client,
                    article,
                    settings.servicenow_kb_id,
                    category_mapping=category_mapping,
                )
                results.append({"article_id": article.article_id, "outcome": outcome})
            except ServiceNowKBError as err:
                logger.error(
                    "article_publish_failed",
                    article_id=article.article_id,
                    error=str(err),
                )
                failed.append({"article_id": article.article_id, "error": str(err)})
    finally:
        client.close()

    created = sum(1 for r in results if r["outcome"] == "created")
    updated = sum(1 for r in results if r["outcome"] == "updated")
    _write_report(
        args.report,
        {
            "dry_run": False,
            "created": created,
            "updated": updated,
            "failed": failed,
            "results": results,
        },
    )

    if failed:
        logger.error(
            "publishing_failed",
            created=created,
            updated=updated,
            failed=len(failed),
            report=str(args.report),
        )
        return 1

    logger.info(
        "publishing_complete",
        created=created,
        updated=updated,
        failed=0,
        report=str(args.report),
    )
    return 0


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("report_written", report=str(path))


if __name__ == "__main__":
    sys.exit(main())
