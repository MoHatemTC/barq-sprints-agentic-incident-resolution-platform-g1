"""End-to-end pipeline: semantic manual sections → Articles → Qdrant + ServiceNow KB.

Reads data/corpus/manual_semantic_sections.json (the pre-rendered corpus
produced by scripts/corpus_build/build_semantic_corpus.py — one flattened
``text`` field per section) and maps every section onto one ``Article``:

* incident articles live only in section 6 of the manual. A subsection whose
  title matches a canonical barq_articles.json article (``6.4`` "KB0001 —
  VPN authentication fails…" … ``6.13`` "KB0010 — Order service…") keeps that
  article's full identity — number, version, title, category, service,
  workflow_state, security_level — so the manual's rendering replaces the
  same article_id instead of forking it; only the body is taken from the
  manual. Subsections that merely reference an incident (``6.3`` "KB0005 —
  the archived scan", which points at the published text in 6.8) do not
  match the canonical title and are treated as ordinary manual content;
* every other section gets its number derived from its own section number:
  chapter and minor are zero-padded into the model's ``KB\\d{4}`` contract —
  ``3.4`` → KB0304, chapter-only ``6`` → KB0600 — and lettered appendices
  continue the sequence after the last chapter (``A`` → KB1300, ``B.3`` →
  KB1403, ``E`` → KB1700). The derived range is disjoint from the canonical
  KB0001..KB0010 corpus and the human-captured KB1xxx articles;
* non-incident sections carry ``workflow_state="published"`` and
  ``security_level="restricted"`` so the manual stays isolated from the
  agent's current retrieval filters until it is deliberately widened, and
  ``category="process"`` / ``service="general"`` give the agent a stable
  future handle: widening a service filter to
  ``["<incident_service>", "general"]`` is enough to opt in.

The articles are then chunked/embedded into the main ``incident_knowledge_base``
Qdrant collection via :func:`app.retrieval.ingest.ingest_articles` —
replace-per-article, so existing KB articles are never touched or purged.
Only the manual-section articles are then published to the ServiceNow KB with
the same idempotent publisher ``scripts/publish_kb.py`` uses (preflight
resolves/creates the ``process`` category, then each article is created or
updated by its stable source id). The incident articles are NOT republished:
they already exist in ServiceNow under their canonical body, the kb_publisher
account cannot modify published rows (the fail-closed write guard refuses the
stale-making text change), and the manual's rendering is Qdrant-only by design.

Read-only by default, matching scripts/publish_kb.py after #76 (issue #41):
without ``--allow-writes`` the run only builds and validates the Articles.
Old-data cleanup is a separate prerequisite — run this only after it is done.

Run with: .venv/bin/python scripts/manual/pipeline_semantic_ingest.py [--allow-writes]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from qdrant_client import QdrantClient

from app.core.config import (
    get_retrieval_settings,
    get_settings,
    kb_publisher_settings,
)
from app.models.knowledge import Article, SecurityLevel, WorkflowState
from app.publishing.exceptions import ServiceNowKBError
from app.publishing.servicenow_kb import ServiceNowKBClient, publish_article
from app.retrieval.chunking import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE
from app.retrieval.embedding import FastEmbedEngine
from app.retrieval.ingest import ingest_articles

SOURCE_PATH = Path("data/corpus/manual_semantic_sections.json")
INCIDENT_CORPUS_PATH = Path("data/corpus/barq_articles.json")

#: The manual's last numbered chapter is 12; lettered appendices continue from
#: here (A → 13, B → 14, …) so every section maps into the KB\d{4} contract.
APPENDIX_BASE = 13

#: Section-6 subsection titles that ARE an incident article: "KB0007 — Laptop…".
#: The archived-scan narrative (6.3) also matches this shape, which is why the
#: canonical title cross-check below — not the prefix alone — decides.
_INCIDENT_TITLE = re.compile(r"^KB(\d{4})\s*[—–-]\s*(.+)$")


def _version_tuple(version: str) -> tuple[int, int]:
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def load_latest_incident_articles() -> dict[str, Article]:
    """Canonical incident articles by number, highest version wins.

    KB0010 exists as v1.0 (retired) and v2.0 (published); the manual's section
    6.13 prints the published text, so it must replace v2.0, not fork a new
    v1.0 identity alongside it.
    """
    entries = json.loads(INCIDENT_CORPUS_PATH.read_text(encoding="utf-8"))
    latest: dict[str, Article] = {}
    for entry in entries:
        article = Article.model_validate(entry)
        current = latest.get(article.article_number)
        if current is None or _version_tuple(article.version) > _version_tuple(current.version):
            latest[article.article_number] = article
    return latest


def match_incident_article(title: str, incidents: dict[str, Article]) -> Article | None:
    """Return the canonical article this section prints, if it is that article.

    Both the KB prefix and the title after it must match the canonical corpus
    (case-insensitive): 6.8 "KB0005 — Account is locked…" matches KB0005,
    while 6.3 "KB0005 — the archived scan" does not and stays manual content.
    """
    m = _INCIDENT_TITLE.match(title.strip())
    if not m:
        return None
    candidate = incidents.get(f"KB{m.group(1)}")
    if candidate is not None and candidate.title.strip().lower() == m.group(2).strip().lower():
        return candidate
    return None


def article_number_for_section(section_number: str) -> str:
    """Derive the article number from the section's own number.

    ``3.4`` → KB0304, chapter-only ``6`` → KB0600, ``B.3`` → KB1403. Raises
    for numbers that cannot fit the model's four-digit contract (a chapter
    above 99, an appendix past Z) — those must stop the pipeline, not alias
    an existing article.
    """
    parts = section_number.split(".")
    head = parts[0]
    if head.isdigit():
        major = int(head)
    else:
        major = APPENDIX_BASE + (ord(head.upper()) - ord("A"))
    minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0

    number = major * 100 + minor
    if not 1 <= number <= 9999:
        raise ValueError(
            f"section {section_number!r} cannot map into the KB\\d{{4}} "
            f"article-number contract (derived {number})"
        )
    return f"KB{number:04d}"


def build_articles() -> tuple[list[Article], set[str]]:
    """Map every semantic manual section onto an Article numbered per section.

    Section-6 subsections that print a canonical incident article keep that
    article's identity (number, version, title, metadata) with a body taken
    from the manual; every other section is numbered from its section number
    and tagged process/general/published/restricted.

    Returns:
        All articles in corpus order, plus the article_ids of the incident
        articles whose canonical ServiceNow rows must not be republished.

    Raises:
        FileNotFoundError / KeyError / ValidationError: propagates — a corpus
            that cannot produce valid Articles must stop the pipeline, not
            publish a partial range.
    """
    raw = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    incidents = load_latest_incident_articles()

    articles: list[Article] = []
    incident_ids: set[str] = set()
    seen_numbers: dict[str, str] = {}
    for item in raw["sections"]:
        section_number = str(item["section_number"])
        title = str(item["title"])
        text = str(item["text"])

        incident = match_incident_article(title, incidents)
        if incident is not None:
            article_number, version = incident.article_number, incident.version
            title = incident.title[:200]
            incident_ids.add(f"{article_number}-v{version}")
        else:
            article_number = article_number_for_section(section_number)
            version = "1.0"
            title = title.strip()[:200]
            if len(title) < 5:  # Article.title floor; manual titles only need >= 1
                title = f"Manual section {section_number}"

        if article_number in seen_numbers:
            raise ValueError(
                f"sections {seen_numbers[article_number]!r} and {section_number!r} "
                f"both map to {article_number}; the mapping is no longer collision-free"
            )
        seen_numbers[article_number] = section_number

        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")

        articles.append(
            Article(
                article_number=article_number,
                version=version,
                title=title,
                short_description=first_line[:250] or "Operations manual section",
                body=text,
                category=incident.category if incident else "process",
                service=incident.service if incident else "general",
                workflow_state=incident.workflow_state if incident else WorkflowState.PUBLISHED,
                security_level=incident.security_level if incident else SecurityLevel.RESTRICTED,
            )
        )
    return articles, incident_ids


def ingest_to_qdrant(articles: list[Article]) -> int:
    """Chunk, embed, and upsert the articles into the main KB collection."""
    settings = get_retrieval_settings()
    engine = FastEmbedEngine()
    client = QdrantClient(url=settings.qdrant_url)
    # force_recreate=False and no purge: the shared incident_knowledge_base
    # keeps every existing article; ingestion is strictly additive per article.
    # Chunking uses chunking.py's constants (not ingest_articles' 700-char
    # legacy defaults) so the whole corpus chunks by one source of truth, and
    # split_on_headers=False keeps each manual section one chunk unless it
    # exceeds the size cap — the section is already the semantic unit.
    upserted = ingest_articles(
        articles,
        client,
        settings.qdrant_collection_name,
        embedding_engine=engine,
        chunk_size=DEFAULT_CHUNK_SIZE,
        chunk_overlap=DEFAULT_CHUNK_OVERLAP,
        force_recreate=False,
        split_on_headers=False,
    )
    return upserted


async def publish_to_servicenow(articles: list[Article]) -> int:
    """Publish the manual-section articles to the ServiceNow KB; returns failures.

    Mirrors scripts/publish_kb.py: preflight resolves (or creates) the
    ``process`` category under the target KB, then each article is published
    idempotently by its stable source id with fail-closed read-back checks.
    Callers pass only manual-section articles — the canonical incident rows
    are already published in ServiceNow and frozen for the kb_publisher
    account, so republishing them would be refused by design.
    """
    settings = kb_publisher_settings(get_settings())
    if not settings.servicenow_kb_id:
        print(
            "Error: SERVICENOW_KB_ID is not set. Create the Knowledge Base and set it "
            "in .env before publishing.",
            file=sys.stderr,
        )
        return 1

    failed = 0
    client = ServiceNowKBClient(settings)
    try:
        category_mapping = await client.run_preflight(
            settings.servicenow_kb_id, sorted({a.category for a in articles})
        )
        for article in articles:
            try:
                outcome = await publish_article(
                    client, article, settings.servicenow_kb_id, category_mapping=category_mapping
                )
                print(f"  {article.article_id}: {outcome}")
            except ServiceNowKBError as err:
                failed += 1
                print(f"  {article.article_id}: FAILED — {err}", file=sys.stderr)
    finally:
        await client.aclose()
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="Actually write to Qdrant and ServiceNow. Without this the run is a dry run "
        "that only builds and validates the Articles.",
    )
    args = parser.parse_args()

    if not SOURCE_PATH.exists():
        print(
            f"Error: semantic corpus not found at {SOURCE_PATH}. "
            "Run scripts/corpus_build/build_semantic_corpus.py first.",
            file=sys.stderr,
        )
        return 1

    print("Mapping sections to Article models...")
    articles, incident_ids = build_articles()
    print(
        f"Generated {len(articles)} Articles: {len(incident_ids)} incident articles keeping "
        f"their canonical numbers/versions/metadata (section 6), "
        f"{len(articles) - len(incident_ids)} manual sections numbered by section "
        f"(process/general/published/restricted)"
    )

    if not args.allow_writes:
        print("Dry run complete — re-run with --allow-writes to ingest and publish.")
        return 0

    print("Ingesting to Qdrant...")
    upserted = ingest_to_qdrant(articles)
    print(f"Qdrant ingestion complete: {upserted} chunk points upserted.")

    manual_articles = [a for a in articles if a.article_id not in incident_ids]
    print(
        f"Publishing {len(manual_articles)} manual-section articles to ServiceNow "
        f"(skipping {len(incident_ids)} incident articles already published there — "
        f"published rows are frozen, the manual's rendering is Qdrant-only)..."
    )
    failed = asyncio.run(publish_to_servicenow(manual_articles))
    if failed:
        print(f"ServiceNow publishing finished with {failed} failure(s).", file=sys.stderr)
        return 1
    print("ServiceNow publishing complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
