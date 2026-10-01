"""Benchmark the manual collections x retrieval modes in ONE process.

Loads the embedding models once, then runs the full 100-turn Stage A dataset
against each (collection, mode) combination:

    collections: manual_semantic_sections (flattened "label: text" tables)
                 manual_markdown_sections (raw blocks, to_semantic_text tables)
    modes:       dense_only | hybrid (dense+BM25 RRF) | hybrid_reranked (RRF + cross-encoder)

Run with: .venv/bin/python scripts/benchmark_manual_collections.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# Resolve .env-dependent settings while the CWD is still the repo root.
from app.core.config import RetrievalMode, get_retrieval_settings  # noqa: E402

QDRANT_URL = get_retrieval_settings().qdrant_url

from app.retrieval.embedding import FastEmbedEngine  # noqa: E402
from qdrant_client import QdrantClient  # noqa: E402

# The corpus adapter opens barq_rag_eval_dataset.json relative to the CWD.
os.chdir(REPO / "data" / "corpus")
sys.path.insert(0, str(REPO / "data" / "corpus"))
import adapters as A  # noqa: E402

os.chdir(REPO)
from scripts.smoke_eval_retrieval import mean_reciprocal_rank, search  # noqa: E402

COMBINATIONS = [
    ("manual_semantic_sections", RetrievalMode.DENSE_ONLY),
    ("manual_semantic_sections", RetrievalMode.HYBRID),
    ("manual_semantic_sections", RetrievalMode.HYBRID_RERANKED),
    ("manual_markdown_sections", RetrievalMode.DENSE_ONLY),
    ("manual_markdown_sections", RetrievalMode.HYBRID),
    ("manual_markdown_sections", RetrievalMode.HYBRID_RERANKED),
]


def run_cell(client, engine, collection, mode, turns) -> dict:
    results = []
    for i, t in enumerate(turns, 1):
        hits = search(client, engine, mode, collection, t["standalone_input"], 5)
        retrieved = [h.article_number for h in hits]
        s = A.score_retrieval(retrieved, t)
        if t["expected_behaviour"] == "answer":
            results.append((s["recall"], s["precision"], mean_reciprocal_rank(retrieved, t), s["clean"]))
        else:
            results.append((None, None, None, not (set(t["must_not_retrieve"]) & set(retrieved))))
        if i % 20 == 0:
            print(f"    {collection}/{mode.value}: {i}/{len(turns)} turns...", flush=True)

    answered = [r for r in results if r[0] is not None]
    return {
        "recall": sum(r[0] for r in answered) / len(answered),
        "precision": sum(r[1] for r in answered) / len(answered),
        "mrr": sum(r[2] for r in answered) / len(answered),
        "full_recall": sum(1 for r in answered if r[0] == 1.0),
        "answered": len(answered),
        "clean": sum(1 for r in results if r[3]),
        "total": len(results),
    }


def main() -> None:
    turns = A.turns()
    print(f"dataset: {len(turns)} turns | qdrant: {QDRANT_URL}")
    engine = FastEmbedEngine()
    client = QdrantClient(url=QDRANT_URL)
    print("models loaded — running benchmark cells\n")

    print(f"{'collection':30s} {'mode':17s} {'recall':>6s} {'prec':>5s} {'MRR':>6s}  full-recall  clean")
    for collection, mode in COMBINATIONS:
        stats = run_cell(client, engine, collection, mode, turns)
        print(f"{collection:30s} {mode.value:17s} "
              f"{stats['recall']:6.2f} {stats['precision']:5.2f} {stats['mrr']:6.3f}  "
              f"{stats['full_recall']:3d}/{stats['answered']:3d}      "
              f"{stats['clean']}/{stats['total']}", flush=True)


if __name__ == "__main__":
    main()
