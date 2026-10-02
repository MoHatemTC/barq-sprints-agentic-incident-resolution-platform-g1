"""Smoke-test Stage A retrieval for barq_rag_eval_dataset.json against Qdrant.

Takes the first 5 and last 5 turns of the 100-turn Stage A dataset (or all of
them with --all), runs each standalone question through a manual-sections
Qdrant collection, and scores the results with the adapter's deterministic
score_retrieval() (no LLM judge). Refusal/clarification turns are reported
but not scored — retrieval is not applicable to them pending Stage B.

Also serves as the shared retrieval helper module for the other eval scripts
(benchmark_manual_collections.py, diagnose_manual_retrieval.py): search(),
to_hit() and mean_reciprocal_rank() live here.

Run with:
  .venv/bin/python scripts/smoke_eval_retrieval.py                       # 10-turn smoke
  .venv/bin/python scripts/smoke_eval_retrieval.py --all                 # full dataset
  .venv/bin/python scripts/smoke_eval_retrieval.py --all --mode hybrid_reranked
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Fusion, FusionQuery, Prefetch, SparseVector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.clients.qdrant import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME
from app.core.config import RetrievalMode, get_retrieval_settings
from app.retrieval.embedding import FastEmbedEngine
from app.retrieval.hybrid_search import RetrievalHit

_QDRANT_URL = get_retrieval_settings().qdrant_url

_CORPUS_DIR = Path(__file__).resolve().parents[1] / "data" / "corpus"
sys.path.insert(0, str(_CORPUS_DIR))
import adapters as A  # noqa: E402

TOP_N = 5
BOTTOM_N = 5
TOP_K = 5
_RERANK_TITLE = os.environ.get("SMOKE_RERANK_TITLE", "1") != "0"


def to_hit(point) -> RetrievalHit:
    """Wrap a point payload so the production reranker can consume it.

    Two payload schemas exist:
    * the dedicated manual-section collections store ``section_number`` /
      ``section_title``; the title carries "number + title" so the reranker
      reads the same document text the embedder saw — see rerank.py;
    * the main ``incident_knowledge_base`` collection stores the article
      schema (``article_number`` / ``title``), where production passes the
      payload title through unchanged — see hybrid_search.to_hit.
    """
    p = point.payload or {}
    if p.get("article_number") is not None:
        number = str(p.get("article_number") or "")
        doc_title = str(p.get("title") or "")
        title = doc_title if _RERANK_TITLE else ""
    else:
        number = str(p.get("section_number") or "")
        doc_title = str(p.get("section_title") or "")
        title = f"{number} {doc_title}".strip() if _RERANK_TITLE else ""
    return RetrievalHit(
        score=float(point.score),
        article_id=str(p.get("section_id") or p.get("article_id") or ""),
        article_number=number,
        version="4.0",
        title=title,
        section=doc_title,
        chunk_index=int(p.get("chunk_index", 0)),
        chunk_text=p.get("chunk_text", ""),
        workflow_state="published",
        security_level="internal",
        category="manual",
    )


def search(
    client: QdrantClient,
    engine: FastEmbedEngine,
    mode: RetrievalMode,
    collection: str,
    query: str,
    k: int,
) -> list[RetrievalHit]:
    """Mirror the production retrieval path: dense-only, hybrid RRF fusion, or
    hybrid + cross-encoder rerank (the reranker blends fusion rank with
    encoder rank — see app.retrieval.rerank).
    """
    embedded = engine.embed_query(query)
    if mode is RetrievalMode.DENSE_ONLY:
        points = client.query_points(
            collection_name=collection,
            query=embedded.dense,
            using=DENSE_VECTOR_NAME,
            limit=k,
            with_payload=True,
        ).points
        return [to_hit(p) for p in points]

    needs_rerank = mode is RetrievalMode.HYBRID_RERANKED
    fetch_limit = max(k * 4, 20) if needs_rerank else k
    prefetch_limit = max(fetch_limit * 4, 20)
    response = client.query_points(
        collection_name=collection,
        prefetch=[
            Prefetch(
                query=embedded.dense,
                using=DENSE_VECTOR_NAME,
                limit=prefetch_limit,
            ),
            Prefetch(
                query=SparseVector(
                    indices=embedded.sparse_indices,
                    values=embedded.sparse_values,
                ),
                using=SPARSE_VECTOR_NAME,
                limit=prefetch_limit,
            ),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=fetch_limit,
        with_payload=True,
    )
    points = response.points if hasattr(response, "points") else response
    hits = [to_hit(p) for p in points]
    if needs_rerank and hits:
        from app.retrieval.rerank import get_default_reranker

        return get_default_reranker().rerank(query, hits, top_n=k)
    return hits[:k]


def mean_reciprocal_rank(retrieved: list[str], t: dict) -> float | None:
    want = set(t["expected_sections"]) - {"—"}
    if not want:
        return None
    for rank, section in enumerate(retrieved, start=1):
        if section in want:
            return 1.0 / rank
    return 0.0


def section_to_article_map() -> dict[str, str]:
    """Dataset section numbers → KB article numbers in incident_knowledge_base.

    Derived with the exact same logic the ingest pipeline used: a section
    whose title prints a canonical incident article keeps that article's
    number (``6.8`` → KB0005), every other section is numbered from itself
    (``1.1`` → KB0101, ``B.3`` → KB1403). Importing the pipeline module is
    safe — it only defines constants and functions at import time.
    """
    from scripts.manual.pipeline_semantic_ingest import (
        article_number_for_section,
        load_latest_incident_articles,
        match_incident_article,
    )

    corpus = _CORPUS_DIR / "manual_semantic_sections.json"
    raw = json.loads(corpus.read_text(encoding="utf-8"))
    incidents = load_latest_incident_articles()
    mapping: dict[str, str] = {}
    for item in raw["sections"]:
        section_number = str(item["section_number"])
        incident = match_incident_article(str(item["title"]), incidents)
        mapping[section_number] = (
            incident.article_number if incident else article_number_for_section(section_number)
        )
    return mapping


def align_labels(t: dict, retrieved: list[str], section_map: dict[str, str]) -> dict:
    """Translate the dataset's section-number labels when comparing.

    The dataset labels ground truth with manual section numbers (``1.1``);
    the main collection labels its hits with KB article numbers (``KB0101``).
    When the retriever returned KB numbers, map the expected and forbidden
    lists into the same space so score_retrieval/mean_reciprocal_rank compare
    like with like. Section-number collections need no translation.
    """
    if not any(r.startswith("KB") for r in retrieved):
        return t
    return {
        **t,
        "expected_sections": [section_map.get(s, s) for s in t["expected_sections"]],
        "must_not_retrieve": [section_map.get(s, s) for s in t.get("must_not_retrieve", [])],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default="manual_semantic_sections")
    parser.add_argument("--url", default=_QDRANT_URL)
    parser.add_argument("--all", action="store_true", help="run the full 100-turn dataset")
    parser.add_argument(
        "--mode",
        choices=[m.value for m in RetrievalMode],
        default=RetrievalMode.DENSE_ONLY.value,
        help="dense_only (default) | hybrid | hybrid_reranked (production path)",
    )
    args = parser.parse_args()
    mode = RetrievalMode(args.mode)

    all_turns = A.turns()
    behaviours = collections.Counter(t["expected_behaviour"] for t in all_turns)
    print(f"dataset: {len(all_turns)} turns, behaviours {dict(behaviours)}")
    if args.all:
        turns = all_turns
        print(f"running the FULL dataset: {len(turns)} turns\n")
    else:
        turns = all_turns[:TOP_N] + all_turns[-BOTTOM_N:]
        print(f"sampling {len(turns)} turns ({TOP_N} from top + {BOTTOM_N} from bottom)\n")

    engine = FastEmbedEngine()
    client = QdrantClient(url=args.url)
    points_count = client.get_collection(collection_name=args.collection).points_count
    print(f"searching '{args.collection}' ({points_count} points) in mode={mode.value}\n")

    section_map = section_to_article_map()

    scored_records = []
    t0 = time.perf_counter()
    for i, t in enumerate(turns, start=1):
        hits = search(client, engine, mode, args.collection, t["standalone_input"], TOP_K)
        retrieved = [h.article_number for h in hits]
        t_scored = align_labels(t, retrieved, section_map)
        mrr = mean_reciprocal_rank(retrieved, t_scored)
        s = A.score_retrieval(retrieved, t_scored)

        if i % 10 == 0:
            print(f"    ... {i}/{len(turns)} ({time.perf_counter() - t0:.0f}s elapsed)", flush=True)

        if t["expected_behaviour"] == "answer":
            passed = s["recall"] == 1.0 and s["clean"]
            record = {
                "turn_id": t["turn_id"],
                "behaviour": t["expected_behaviour"],
                "mrr": mrr,
                "query": t["standalone_input"],
                "expected": t["expected_sections"],
                "expected_as_articles": t_scored["expected_sections"],
                "retrieved": retrieved,
                "passed": passed,
                "recall": s["recall"],
                "precision": s["precision"],
                "clean": s["clean"],
            }
            scored_records.append(record)
            print(f"[{t['turn_id']}] (answer, {t.get('difficulty')}) {t['standalone_input'][:70]}")
            print(f"    expected:  {t['expected_sections']}")
            print(
                "    retrieved: " + ", ".join(f"{h.article_number} ({h.score:.4f})" for h in hits)
            )
            print(
                f"    -> recall={s['recall']:.2f} precision={s['precision']:.2f} "
                f"mrr={mrr:.3f} forbidden={s.get('forbidden_retrieved', [])}"
            )
        else:
            clean = not (set(t_scored.get("must_not_retrieve", [])) & set(retrieved))
            record = {
                "turn_id": t["turn_id"],
                "behaviour": t["expected_behaviour"],
                "mrr": mrr,
                "query": t["standalone_input"],
                "expected": t["expected_sections"],
                "retrieved": retrieved,
                "passed": clean,
                "clean": clean,
            }
            scored_records.append(record)
            print(
                f"[{t['turn_id']}] ({t['expected_behaviour']}, {t.get('difficulty')}) "
                f"{t['standalone_input'][:70]}"
            )
            forbidden_info = s.get("forbidden_retrieved") or "none"
            print(
                "    -> retrieval not applicable pending Stage B; "
                f"forbidden retrieved: {forbidden_info}"
            )

    answers = [r for r in scored_records if r["behaviour"] == "answer"]
    avg_recall = sum(r["recall"] for r in answers) / max(1, len(answers))
    avg_precision = sum(r["precision"] for r in answers) / max(1, len(answers))
    avg_mrr = sum(r["mrr"] for r in answers if r["mrr"] is not None) / max(1, len(answers))
    rank_1 = sum(1 for r in answers if r["mrr"] == 1.0)
    clean_all = sum(1 for r in scored_records if r.get("clean"))

    print(
        f"\nscored {len(answers)} answer turns | avg recall {avg_recall:.2f} | "
        f"avg precision {avg_precision:.2f} | avg MRR {avg_mrr:.3f} | "
        f"rank-1 hits {rank_1}/{len(answers)}"
    )
    print(f"forbidden-content clean: {clean_all}/{len(scored_records)}")
    print("\nper-capability pass rates over the sample:")
    for cap, stat in A.slice_report(scored_records).items():
        print(f"  {cap:24s} {stat['passed']}/{stat['total']}  ({stat['rate']:.0%})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
