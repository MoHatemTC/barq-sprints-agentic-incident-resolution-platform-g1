"""Seed a new Qdrant collection from the flattened semantic manual corpus.

Reads data/corpus/manual_semantic_sections.json (produced by
scripts/build_semantic_corpus.py — one pre-rendered `text` field per section,
no block structure), chunks it with the standard manual chunker, embeds with
FastEmbed (dense bge-small + sparse BM25, single batch call) and upserts
deterministic points into a dedicated collection, then runs a live search to
prove Qdrant retrieves over it.

Run with: .venv/bin/python scripts/seed_qdrant_semantic.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from qdrant_client import QdrantClient

from app.core.config import get_retrieval_settings
from app.models.manual_section import ManualSection, TextBlock
from app.retrieval.embedding import FastEmbedEngine
from app.retrieval.extraction.parse_appendix import AppendixERelationships
from app.retrieval.manual.manual_chunking import chunk_sections
from app.retrieval.manual.manual_ingest import ingest_manual_sections

DEFAULT_SEMANTIC_CORPUS = Path("data/corpus/manual_semantic_sections.json")
DEFAULT_COLLECTION = "manual_semantic_sections"

# The semantic corpus stores pre-rendered text (one `text` field per section);
# the raw markdown corpus stores structured blocks. Either way the chunker
# consumes a ManualSection — flattened text goes in a single TextBlock, real
# blocks validate directly and render through TableBlock.to_semantic_text().
SEMANTIC_FIELDS = {"section_id", "section_number", "title", "text", "content_type", "pages"}


def section_from_json(s: dict) -> ManualSection:
    if "text" in s:
        return ManualSection(
            section_id=s["section_id"],
            section_number=s["section_number"],
            title=s["title"],
            blocks=[TextBlock(text=s["text"])],
            content_type=s["content_type"],
            pages=tuple(s["pages"]),
        )
    return ManualSection.model_validate(s)


def main() -> int:
    settings = get_retrieval_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_SEMANTIC_CORPUS)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--url", default=settings.qdrant_url)
    parser.add_argument("--force-recreate", action="store_true")
    parser.add_argument("--query", default="how fast must we respond to a priority 1 incident?")
    args = parser.parse_args()

    if not args.corpus.exists():
        print(
            f"Error: semantic corpus not found at {args.corpus}. "
            "Run scripts/build_semantic_corpus.py first.",
            file=sys.stderr,
        )
        return 1

    raw = json.loads(args.corpus.read_text(encoding="utf-8"))
    sections = [section_from_json(s) for s in raw["sections"]]
    relationships = AppendixERelationships(
        forward=raw["relationships"].get("forward", {}),
        reverse=raw["relationships"].get("reverse", {}),
    )
    print(f"loaded {len(sections)} semantic sections from {args.corpus}")

    chunks = chunk_sections(sections, relationships)
    print(f"chunked into {len(chunks)} chunks")

    engine = FastEmbedEngine()
    client = QdrantClient(url=args.url)
    upserted = ingest_manual_sections(
        chunks=chunks,
        client=client,
        collection_name=args.collection,
        embedding_engine=engine,
        force_recreate=args.force_recreate,
    )
    stored = client.get_collection(collection_name=args.collection).points_count
    print(f"upserted {upserted} points into '{args.collection}' (collection now holds {stored})")

    # Live search to prove the collection retrieves.
    q = engine.embed_query(args.query)
    hits = client.query_points(
        collection_name=args.collection,
        query=q.dense,
        using="dense",
        limit=3,
        with_payload=True,
    ).points
    print(f'\ntop 3 for "{args.query}":')
    for hit in hits:
        p = hit.payload or {}
        print(f"  {hit.score:.4f}  {p.get('section_id')} — {p.get('section_title')}")

    if not hits:
        print("Error: search returned no hits", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
