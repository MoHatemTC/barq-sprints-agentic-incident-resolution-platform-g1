#!/usr/bin/env python3
"""Single-command ablation harness: dense-only vs hybrid vs hybrid+reranked.

Runs every incident in `eval/evaluation_set.json` through all three
retrieval modes against the live, already-seeded Qdrant collection.
Usage:
    uv run python eval/ablation.py
    uv run python eval/ablation.py --limit 5 --seed 42
    uv run python eval/ablation.py --collection incident_knowledge_base \\
        --max-security-level restricted --output eval/ablation_results.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from qdrant_client import QdrantClient

from app.core.config import RetrievalMode, get_retrieval_settings
from app.models.knowledge import SecurityLevel
from app.retrieval.embedding import FastEmbedEngine
from app.retrieval.filters import MetadataFilterBuilder
from app.retrieval.hybrid_search import timed_hybrid_search

EVAL_SET_PATH = Path("eval/evaluation_set.json")
MODES: list[RetrievalMode] = [
    RetrievalMode.DENSE_ONLY,
    RetrievalMode.HYBRID,
    RetrievalMode.HYBRID_RERANKED,
]

LOW_CONFIDENCE_THRESHOLDS: dict[RetrievalMode, float] = {
    RetrievalMode.DENSE_ONLY: 0.30,
    RetrievalMode.HYBRID: 0.05,
    RetrievalMode.HYBRID_RERANKED: 0.0,
}


@dataclass
class IncidentScore:
    incident_id: str
    is_answerable: bool
    precision: float
    recall: float
    correct: bool
    forbidden_hit: bool
    hit_at_1: bool
    hit_at_5: bool
    relevant_rank: int | None
    latency_ms: float
    returned_article_ids: list[str] = field(default_factory=list)


def score_incident(
    incident: dict,
    hit_article_ids: list[str],
    top_score: float,
    low_confidence_threshold: float,
) -> IncidentScore:
    """Compute precision, recall, and other metrics for a single incident."""
    ground_truth: dict[str, float] = {a: 1.0 for a in incident["primary_article_ids"]}
    for a in incident["acceptable_article_ids"]:
        ground_truth.setdefault(a, 0.5)
    forbidden = set(incident["forbidden_article_ids"])
    is_answerable = incident["is_answerable"]

    if forbidden & set(hit_article_ids):
        return {
            "precision": 0.0,
            "recall": 0.0,
            "correct": False,
            "forbidden_hit": True,
            "hit_at_1": False,
            "hit_at_5": False,
            "relevant_rank": None,
        }

    if not is_answerable:
        refused = (not hit_article_ids) or top_score < low_confidence_threshold
        value = 1.0 if refused else 0.0
        return {
            "precision": value,
            "recall": value,
            "correct": refused,
            "forbidden_hit": False,
            "hit_at_1": False,
            "hit_at_5": False,
            "relevant_rank": None,
        }

    if not hit_article_ids:
        return {
            "precision": 0.0,
            "recall": 0.0,
            "correct": False,
            "forbidden_hit": False,
            "hit_at_1": False,
            "hit_at_5": False,
            "relevant_rank": None,
        }

    per_hit_value = [ground_truth.get(a, 0.0) for a in hit_article_ids]
    precision = sum(per_hit_value) / len(hit_article_ids)

    distinct_hits = set(hit_article_ids)
    recovered = sum(ground_truth[a] for a in distinct_hits if a in ground_truth)
    max_possible = sum(ground_truth.values())
    recall = recovered / max_possible if max_possible else 0.0

    relevant_rank = next(
        (i for i, a in enumerate(hit_article_ids, start=1) if a in ground_truth), None
    )

    return {
        "precision": precision,
        "recall": recall,
        "correct": recall > 0.0,
        "forbidden_hit": False,
        "hit_at_1": relevant_rank == 1,
        "hit_at_5": relevant_rank is not None and relevant_rank <= 5,
        "relevant_rank": relevant_rank,
    }


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * (pct / 100)
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    if lo == hi:
        return values[lo]
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def run_mode(
    client: QdrantClient,
    engine: FastEmbedEngine,
    incidents: list[dict],
    mode: RetrievalMode,
    limit: int,
    filter_builder: MetadataFilterBuilder,
) -> list[IncidentScore]:
    threshold = LOW_CONFIDENCE_THRESHOLDS[mode]

    if incidents:
        timed_hybrid_search(
            client,
            incidents[0]["query"],
            limit=limit,
            metadata=filter_builder,
            engine=engine,
            mode=mode,
        )

    scores: list[IncidentScore] = []
    for incident in incidents:
        result = timed_hybrid_search(
            client,
            incident["query"],
            limit=limit,
            metadata=filter_builder,
            engine=engine,
            mode=mode,
        )

        # P3 safety-net: fail loudly if a non-published article is EVER
        # returned, independent of whether the eval set's forbidden_article_ids
        # happens to enumerate it.
        for hit in result.hits:
            if hit.workflow_state != "published":
                raise RuntimeError(
                    f"P3 SAFETY VIOLATION: non-published article {hit.article_id} "
                    f"(workflow_state={hit.workflow_state!r}) returned for incident "
                    f"{incident['incident_id']} in mode {mode.value}"
                )

        hit_ids = [h.article_id for h in result.hits]
        top_score = result.hits[0].score if result.hits else 0.0
        outcome = score_incident(incident, hit_ids, top_score, threshold)
        scores.append(
            IncidentScore(
                incident_id=incident["incident_id"],
                is_answerable=incident["is_answerable"],
                latency_ms=result.latency_ms,
                returned_article_ids=hit_ids,
                **outcome,
            )
        )
    return scores


def summarize(scores: list[IncidentScore]) -> dict:
    latencies = [s.latency_ms for s in scores]
    answerable = [s for s in scores if s.is_answerable]
    return {
        "context_precision": round(statistics.mean(s.precision for s in scores), 4),
        "context_recall": round(statistics.mean(s.recall for s in scores), 4),
        "accuracy": round(statistics.mean(1.0 if s.correct else 0.0 for s in scores), 4),
        "hit_at_1": (
            round(statistics.mean(s.hit_at_1 for s in answerable), 4) if answerable else None
        ),
        "hit_at_5": (
            round(statistics.mean(s.hit_at_5 for s in answerable), 4) if answerable else None
        ),
        "forbidden_hit_count": sum(1 for s in scores if s.forbidden_hit),
        "p50_latency_ms": round(percentile(latencies, 50), 2),
        "p95_latency_ms": round(percentile(latencies, 95), 2),
        "mean_latency_ms": round(statistics.mean(latencies), 2),
    }


def find_sparse_rescues(
    dense_scores: list[IncidentScore], hybrid_scores: list[IncidentScore]
) -> list[dict]:
    """
    Incidents where dense-only failed (answerable, incorrect) but hybrid succeeded.
    """
    dense_by_id = {s.incident_id: s for s in dense_scores}
    rescues = []
    for h in hybrid_scores:
        d = dense_by_id[h.incident_id]
        if d.is_answerable and not d.correct and h.correct:
            rescues.append(
                {
                    "incident_id": h.incident_id,
                    "dense_returned_article_ids": d.returned_article_ids,
                    "hybrid_returned_article_ids": h.returned_article_ids,
                }
            )
    return rescues


def compute_rerank_movement(
    hybrid_scores: list[IncidentScore], reranked_scores: list[IncidentScore]
) -> list[dict]:
    """Rank of the first relevant article, hybrid vs. hybrid+reranked.

    `movement > 0` means reranking moved the relevant article UP (toward
    rank 1); `movement < 0` means it moved down. Uses rank rather than raw
    score, since cross-encoder logits are not comparable to RRF scores.
    """
    hybrid_by_id = {s.incident_id: s for s in hybrid_scores}
    movements = []
    for r in reranked_scores:
        h = hybrid_by_id[r.incident_id]
        if h.relevant_rank is not None or r.relevant_rank is not None:
            movement = (
                None
                if h.relevant_rank is None or r.relevant_rank is None
                else h.relevant_rank - r.relevant_rank
            )
            movements.append(
                {
                    "incident_id": r.incident_id,
                    "hybrid_rank": h.relevant_rank,
                    "hybrid_reranked_rank": r.relevant_rank,
                    "movement": movement,
                }
            )
    return movements


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default=None, help="Override QDRANT_COLLECTION_NAME")
    parser.add_argument("--limit", type=int, default=5, help="top_k passed to retrieval")
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help=(
            "Recorded in output for reproducibility metadata. Currently has NO "
            "runtime effect -- no sampling occurs anywhere in this script. "
            "Reserved for a future incident-subsampling mode."
        ),
    )
    parser.add_argument(
        "--max-security-level",
        default="restricted",
        choices=["public", "internal", "restricted"],
        help="Eval incidents span restricted articles (e.g. KB0010); default clears all tiers.",
    )
    parser.add_argument("--output", default="eval/ablation_results.json")
    args = parser.parse_args()

    settings = get_retrieval_settings()
    collection_name = args.collection or settings.qdrant_collection_name
    client = QdrantClient(url=settings.qdrant_url)
    engine = FastEmbedEngine()

    eval_set = json.loads(EVAL_SET_PATH.read_text())
    incidents = eval_set["incidents"]
    max_sec = SecurityLevel(args.max_security_level)
    filter_builder = MetadataFilterBuilder(max_security_level=max_sec)

    per_mode_scores: dict[str, list[IncidentScore]] = {}
    for mode in MODES:
        print(f"[ablation] running mode={mode.value} on {len(incidents)} incidents ...")
        t0 = time.perf_counter()
        per_mode_scores[mode.value] = run_mode(
            client, engine, incidents, mode, args.limit, filter_builder
        )
        print(f"[ablation]   done in {time.perf_counter() - t0:.1f}s")

    summary = {mode.value: summarize(per_mode_scores[mode.value]) for mode in MODES}

    baseline = summary[RetrievalMode.DENSE_ONLY.value]
    best = summary[RetrievalMode.HYBRID_RERANKED.value]
    margin = {
        "context_precision_margin": round(
            best["context_precision"] - baseline["context_precision"], 4
        ),
        "context_recall_margin": round(best["context_recall"] - baseline["context_recall"], 4),
        "accuracy_margin": round(best["accuracy"] - baseline["accuracy"], 4),
    }

    sparse_rescues = find_sparse_rescues(
        per_mode_scores[RetrievalMode.DENSE_ONLY.value],
        per_mode_scores[RetrievalMode.HYBRID.value],
    )
    rerank_movement = compute_rerank_movement(
        per_mode_scores[RetrievalMode.HYBRID.value],
        per_mode_scores[RetrievalMode.HYBRID_RERANKED.value],
    )

    report = {
        "seed": args.seed,
        "collection": collection_name,
        "limit": args.limit,
        "max_security_level": args.max_security_level,
        "modes": [m.value for m in MODES],
        "low_confidence_thresholds": {m.value: LOW_CONFIDENCE_THRESHOLDS[m] for m in MODES},
        "summary": summary,
        "margin_hybrid_reranked_over_dense_only": margin,
        "sparse_rescue_cases": sparse_rescues,
        "rerank_rank_movement": rerank_movement,
        "per_incident": {
            mode.value: [asdict(s) for s in per_mode_scores[mode.value]] for mode in MODES
        },
        "metric_definitions": {
            "context_precision": (
                "Project-graded, chunk-level: mean relevance value (1.0 primary / "
                "0.5 acceptable / 0.0 other) across all returned hits."
            ),
            "context_recall": (
                "Project-graded, article-level set recall: fraction of total "
                "ground-truth relevance weight (primary + acceptable, deduped by "
                "article_id) actually recovered among returned hits."
            ),
            "hit_at_1": (
                "Standard IR: top-ranked hit is a primary/acceptable article "
                "(answerable incidents only)."
            ),
            "hit_at_5": (
                "Standard IR: any hit within the top 5 is a primary/acceptable "
                "article (answerable incidents only)."
            ),
        },
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n")

    print()
    print(json.dumps({"summary": summary, "margin": margin}, indent=2))
    if sparse_rescues:
        print(f"\n{len(sparse_rescues)} sparse-rescue case(s) found:")
        for r in sparse_rescues[:5]:
            print(
                f"  {r['incident_id']}: dense={r['dense_returned_article_ids']} "
                f"-> hybrid={r['hybrid_returned_article_ids']}"
            )
    print(f"\nFull results written to {output_path}")


if __name__ == "__main__":
    main()
