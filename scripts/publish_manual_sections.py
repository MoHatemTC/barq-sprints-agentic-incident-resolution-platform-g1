"""Publish the manifest-adapted manual into the ServiceNow KB.

Read-only by default. Consumes the reviewed manifest (``--manifest``) through
the same preflight the ingestion path uses, then publishes the ``new``
publication units through the existing ``publish_article`` — same publisher
identity, same idempotent ``u_source_id`` lookup, same read-back verification.
No runtime allocation: numbers come from the manifest or nothing is published.

Skipped by design (each logged in the report):
- ``alias`` units — they mirror KB0001–KB0010, which are already published;
- ``index_alias`` units — they produce no article;
- retired units (the archived KB0005 scan) — creating retired rows is a
  release-runbook decision, not a publishing side effect.

Retry semantics are publish_article's: a re-run looks up the same versioned
source ID, creates only missing rows and reports unchanged/updated otherwise,
so a partial publish resumes without duplicates.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import structlog

from app.core.config import Settings, get_settings, kb_publisher_settings
from app.core.logging import configure_logging
from app.publishing.payload import build_kb_payload
from app.publishing.servicenow_kb import (
    ServiceNowKBClient,
    ServiceNowKBError,
    publish_article,
)
from app.retrieval.manual.integration import load_manual_publication
from app.retrieval.manual.manifest import UnitKind, load_manifest

settings = Settings()
configure_logging(environment=settings.environment, log_level=settings.log_level)

logger = structlog.get_logger("publish_manual_sections")

DEFAULT_MANIFEST = Path("data/corpus/manual_kb_manifest.json")
DEFAULT_SECTIONS = Path("data/corpus/manual_sections.json")
DEFAULT_CORPUS = Path("data/corpus/barq_articles.json")
DEFAULT_REPORT = Path("data/corpus/manual_publish_report.json")


def _select_units(publication, manifest):
    """Split publication units into publishable articles and skipped entries."""
    units_by_id = {u.unit_id: u for u in manifest.units}
    publishable, skipped = [], []
    for article in publication.articles:
        provenance = publication.provenance[article.article_id]
        unit = units_by_id[provenance.unit_id]
        if unit.kind is UnitKind.ALIAS:
            skipped.append(
                {
                    "unit_id": unit.unit_id,
                    "article_id": article.article_id,
                    "reason": f"alias of existing corpus article {unit.unique_key}",
                }
            )
        elif unit.workflow_state and unit.workflow_state.value == "retired":
            skipped.append(
                {
                    "unit_id": unit.unit_id,
                    "article_id": article.article_id,
                    "reason": "retired archival unit; publishing it is a runbook decision",
                }
            )
        else:
            publishable.append((article, provenance))
    return publishable, skipped


async def async_main(args: argparse.Namespace) -> int:
    try:
        settings = kb_publisher_settings(get_settings())
    except ValueError as exc:
        logger.error("invalid_publisher_credentials", error=str(exc))
        return 1

    for path in (args.manifest, args.sections, args.corpus):
        if not path.exists():
            print(f"Error: required input not found at {path}.", file=sys.stderr)
            return 1
    if not settings.servicenow_kb_id:
        logger.error("missing_kb_id", remedy="Set SERVICENOW_KB_ID in .env.")
        return 1

    publication = load_manual_publication(args.manifest, args.sections, args.corpus)
    manifest = load_manifest(args.manifest)
    publishable, skipped = _select_units(publication, manifest)
    manifest_sha256 = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    logger.info(
        "manual_publication_selected",
        publishable=len(publishable),
        skipped=len(skipped),
        manifest_sha256=manifest_sha256,
    )

    prior_report: dict | None = None
    if args.report.is_file():
        try:
            prior_report = json.loads(args.report.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prior_report = None

    history = []
    if prior_report:
        if "history" in prior_report and isinstance(prior_report["history"], list):
            history.extend(prior_report["history"])
        if prior_report.get("results") or prior_report.get("failed"):
            history.append(
                {
                    "manifest_sha256": prior_report.get("manifest_sha256"),
                    "results": prior_report.get("results", []),
                    "failed": prior_report.get("failed", []),
                    "dry_run": prior_report.get("dry_run", False),
                }
            )

    report: dict = {
        "manifest": str(args.manifest),
        "manifest_sha256": manifest_sha256,
        "dry_run": args.dry_run,
        "results": [],
        "skipped": skipped,
        "failed": [],
        "history": history,
    }

    if args.dry_run:
        for article, _provenance in publishable:
            payload = build_kb_payload(article, settings.servicenow_kb_id)
            logger.info(
                "payload_validated",
                article_id=article.article_id,
                short_description=payload["short_description"],
                body_html_chars=len(payload["text"]),
            )
        logger.info("dry_run_complete", articles=len(publishable), http_calls=0)
        _write_report(args.report, report)
        return 0

    client = ServiceNowKBClient(settings)
    try:
        unique_categories = sorted({article.category for article, _ in publishable})
        category_mapping = await client.run_preflight(settings.servicenow_kb_id, unique_categories)

        for article, provenance in publishable:
            try:
                outcome = await publish_article(
                    client,
                    article,
                    settings.servicenow_kb_id,
                    category_mapping=category_mapping,
                )
                stored = await client.find_by_source_id(
                    article.article_id, kb_sys_id=settings.servicenow_kb_id
                )
                sys_id = str(stored["sys_id"]) if stored else None
                stored_hash = None
                if sys_id:
                    # The lookup returns list fields only; the body needs a full read.
                    full = await client.get(sys_id)
                    stored_hash = hashlib.sha256(
                        (full.get("text") or "").encode("utf-8")
                    ).hexdigest()
                report["results"].append(
                    {
                        "article_id": article.article_id,
                        "unit_id": provenance.unit_id,
                        "outcome": outcome,
                        "sys_id": sys_id,
                        "stored_body_sha256": stored_hash,
                    }
                )
            except ServiceNowKBError as err:
                logger.error(
                    "article_publish_failed",
                    article_id=article.article_id,
                    error=str(err),
                )
                report["failed"].append(
                    {
                        "article_id": article.article_id,
                        "unit_id": provenance.unit_id,
                        "error": str(err),
                    }
                )
            finally:
                _write_report(args.report, report)
    finally:
        await client.aclose()

    _write_report(args.report, report)
    failed = len(report["failed"])
    logger.info(
        "publish_complete",
        published=len(report["results"]),
        failed=failed,
        skipped=len(skipped),
        report=str(args.report),
    )
    return 1 if failed else 0


def _write_report(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp.{os.getpid()}")
    try:
        temp_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--sections", type=Path, default=DEFAULT_SECTIONS)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and validate payloads without any HTTP traffic. This is the default; "
        "the flag is kept so existing invocations and docs keep working.",
    )
    # Read-only by default, matching scripts/publish_kb.py: the destructive path
    # must be asked for explicitly.
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="Actually publish to ServiceNow. Without this the run is a dry run.",
    )
    args = parser.parse_args()
    if not args.allow_writes:
        args.dry_run = True
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    sys.exit(main())
