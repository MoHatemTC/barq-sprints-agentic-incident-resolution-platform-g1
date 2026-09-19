#!/usr/bin/env python3
"""Measure the evidence gate on the real corpus and embedding model (S2.5).

Ingests ``data/corpus/barq_articles.json`` with FastEmbed into a scratch Qdrant
collection, then scores answerable and out-of-scope incidents through the agent's
retriever. The output is the table in ``docs/sprint2_tracing_and_agent.md`` §4.

    docker compose up -d qdrant
    uv run python scripts/calibrate_retrieval_threshold.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from qdrant_client import QdrantClient

from agent.retrieval import QdrantRetriever
from app.models.knowledge import Classification, SecurityLevel
from app.retrieval.embedding import FastEmbedEngine
from app.retrieval.ingest import ingest_articles
from app.retrieval.sources import LocalJSONSource

#: (name, expected, text, classification). Answerable cases paraphrase the corpus
#: symptoms; the rest are the manual's no-article case, the W0.3 Task 0 set and
#: plainly out-of-scope requests.
CASES: list[tuple[str, str, str, Classification]] = [
    (
        "INC0010023 VPN after password reset",
        "answer",
        "VPN authentication fails after password reset. Can reach the internet but the VPN "
        "client says invalid credentials since I reset my password this morning.",
        Classification.NETWORK,
    ),
    (
        "Outlook disconnected",
        "answer",
        "Outlook shows Disconnected. No new mail has arrived since this morning.",
        Classification.SOFTWARE,
    ),
    (
        "Account locked",
        "answer",
        "Account locked out. My account keeps getting locked after failed sign-ins.",
        Classification.ACCESS,
    ),
    (
        "Mapped drive missing",
        "answer",
        "Shared drive missing. My mapped S: drive is gone after I signed in today.",
        Classification.NETWORK,
    ),
    (
        "Wi-Fi drops on 5 GHz",
        "answer",
        "Wi-Fi keeps dropping. On the 5 GHz corporate network my laptop disconnects.",
        Classification.NETWORK,
    ),
    (
        "W0.3 cannot send email (vague)",
        "ask",
        "Cannot send email. It just doesn't work.",
        Classification.SOFTWARE,
    ),
    (
        "INC0010047 printer grinding (mechanical)",
        "escalate",
        "Printer in meeting room 4 makes a grinding noise. Paper feed grinds.",
        Classification.HARDWARE,
    ),
    (
        "W0.3 printer after office move",
        "answer*",
        "Printer not printing after office move. It was working yesterday.",
        Classification.HARDWARE,
    ),
    (
        "W0.3 annual leave request",
        "escalate",
        "Request: annual leave approval. I would like to take next week off.",
        Classification.OTHER,
    ),
    (
        "Coffee machine leaking",
        "escalate",
        "Coffee machine broken. The coffee machine on floor 3 is leaking water.",
        Classification.HARDWARE,
    ),
    (
        "Excel VLOOKUP question",
        "escalate",
        "Excel formula question. How do I write a VLOOKUP across two sheets?",
        Classification.SOFTWARE,
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--collection", default="s25_calibration")
    parser.add_argument("--threshold", type=float, default=0.55)
    args = parser.parse_args()

    client = QdrantClient(url=args.qdrant_url)
    engine = FastEmbedEngine()
    articles = LocalJSONSource(Path("data/corpus/barq_articles.json")).load_articles()
    chunks = ingest_articles(
        articles,
        client,
        collection_name=args.collection,
        embedding_engine=engine,
        force_recreate=True,
    )
    print(f"model={engine.dense_model_name} chunks={chunks} threshold={args.threshold}\n")

    for level in (SecurityLevel.INTERNAL, SecurityLevel.RESTRICTED):
        retriever = QdrantRetriever(
            lambda: client,
            lambda: engine,
            collection_name=args.collection,
            max_security_level=level,
        )
        print(f"max_security_level = {level.value}\n")
        print("| Incident | Expected | Best cosine | Evidence gate | Top article |")
        print("|---|---|---|---|---|")
        for name, expected, text, label in CASES:
            result = retriever.search(text, classification=label, top_k=5, threshold=args.threshold)
            top = result.hits[0] if result.hits else None
            where = f"{top.article_number} §{top.section}" if top else "—"
            gate = "pass" if result.sufficient else "escalate"
            print(f"| {name} | {expected} | {result.best_relevance:.3f} | {gate} | {where} |")
        print()
    client.delete_collection(args.collection)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
