"""Read-only evaluation of query augmentation and ranking through QdrantRetriever.

Requires the real local models, Qdrant and (with --rewrite) the configured Gemini
proxy. Never invokes ServiceNow or changes the index. Ground truth is the checked-in
article-ID set; manual section stressors remain explicitly excluded until mapped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from qdrant_client import QdrantClient

from agent.config import AgentSettings
from agent.dependencies import AgentDependencies
from agent.llm import LiteLLMClient
from agent.nodes.retrieve import QUERY_CHARS
from agent.query_rewrite import rewrite_query
from agent.retrieval import QdrantRetriever
from app.core.config import RetrievalMode, get_retrieval_settings
from app.models.knowledge import CORPUS_CATEGORY_TO_CLASSIFICATION, Classification, SecurityLevel
from app.retrieval.embedding import FastEmbedEngine
from eval.ablation import partition_evaluation_records
from observability.tracing import Tracer


def snapshot(client: QdrantClient, collection: str) -> tuple[str, list[dict]]:
    """Fingerprint payloads without exporting KB text or access credentials."""
    rows = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection, offset=offset, limit=100, with_payload=True, with_vectors=False
        )
        rows.extend({"id": str(point.id), "payload": point.payload} for point in points)
        if offset is None:
            break
    rows.sort(key=lambda row: row["id"])
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    return digest, [row["payload"] for row in rows]


def summarize(rows: list[dict]) -> dict:
    answerable = [row for row in rows if row["answerable"]]
    unanswerable = [row for row in rows if not row["answerable"]]
    times = sorted(row["latency_ms"] for row in rows)
    return {
        "cases": len(rows),
        "hit_at_1": statistics.mean(row["hit_at_1"] for row in answerable),
        "hit_at_5": statistics.mean(row["hit_at_5"] for row in answerable),
        "mean_primary_recall": statistics.mean(row["primary_recall"] for row in answerable),
        "mrr": statistics.mean(row["reciprocal_rank"] for row in answerable),
        "false_evidence_count": sum(row["sufficient"] for row in unanswerable),
        "unanswerable_cases": len(unanswerable),
        "forbidden_hit_count": sum(row["forbidden_hit"] for row in rows),
        "p50_ms": statistics.median(times),
        "p95_ms": times[min(len(times) - 1, int(len(times) * 0.95))],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default=None)
    parser.add_argument("--rewrite", action="store_true", help="Makes real Gemini calls")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = get_retrieval_settings()
    collection = args.collection or settings.qdrant_collection_name
    tracer = Tracer(None)  # Evidence is local; never export the evaluation KB to tracing.
    agent_settings = AgentSettings(agent_query_rewrite_enabled=args.rewrite)
    client = QdrantClient(url=settings.qdrant_url)
    previous_mode = os.environ.get("RETRIEVAL_MODE")
    try:
        digest, payloads = snapshot(client, collection)
        categories = {p["article_id"]: p["category"] for p in payloads}
        records, excluded = partition_evaluation_records(
            json.loads(Path("eval/evaluation_set.json").read_text())["incidents"]
        )
        engine = FastEmbedEngine()
        retriever = QdrantRetriever(
            lambda: client,
            lambda: engine,
            collection_name=collection,
            max_security_level=SecurityLevel(agent_settings.agent_max_security_level),
        )
        # Only rewrite_query consumes these dependencies; no gateway or registry is invoked.
        deps = AgentDependencies(
            settings=agent_settings,
            llm=LiteLLMClient(agent_settings, tracer) if args.rewrite else None,
            retriever=retriever,
            tools=None,
            tracer=tracer,
        )
        report = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "collection": collection,
            "payload_sha256": digest,
            "point_count": len(payloads),
            "dense_model": settings.dense_embedding_model,
            "sparse_model": settings.sparse_embedding_model,
            "rerank_model": settings.rerank_model,
            "query_model": agent_settings.agent_llm_model,
            "rewrite_timeout_seconds": agent_settings.agent_query_rewrite_timeout_seconds,
            "top_k": agent_settings.agent_retrieval_top_k,
            "threshold": agent_settings.agent_retrieval_threshold,
            "max_security_level": agent_settings.agent_max_security_level,
            "excluded_unmapped_stressors": excluded,
            "missing_primary_ids": sorted(
                {
                    article
                    for row in records
                    for article in row["primary_article_ids"]
                    if article not in categories
                }
            ),
            "classification_source": "primary article category from index; other if absent",
            "latency_scope": "warm models, serial calls, includes one rewrite call where enabled",
            "variants": {},
        }
        queries = []
        for row in records:
            original = row["query"][:QUERY_CHARS]
            start = time.perf_counter()
            augmented = rewrite_query(original, deps) if args.rewrite else original
            rewrite_ms = (time.perf_counter() - start) * 1000
            queries.append((original, augmented, rewrite_ms))
        # These queries come solely from the checked-in evaluation set, not KB payloads.
        report["evaluation_queries"] = [
            {"incident_id": row["incident_id"], "original": original, "augmented": augmented}
            for row, (original, augmented, _) in zip(records, queries, strict=True)
        ]
        for mode in (RetrievalMode.HYBRID, RetrievalMode.HYBRID_RERANKED):
            os.environ["RETRIEVAL_MODE"] = mode.value
            get_retrieval_settings.cache_clear()
            # Model load and first inference are outside the warm-call metrics.
            retriever.search(
                "VPN authentication",
                classification=Classification.NETWORK,
                top_k=5,
                threshold=agent_settings.agent_retrieval_threshold,
            )
            for augment in [False, True] if args.rewrite else [False]:
                scored = []
                for row, (original, augmented, rewrite_ms) in zip(records, queries, strict=True):
                    primary = row["primary_article_ids"]
                    expected = set(primary + row["acceptable_article_ids"])
                    category = categories.get(primary[0], "") if primary else ""
                    label = CORPUS_CATEGORY_TO_CLASSIFICATION.get(category, Classification.OTHER)
                    result = retriever.search(
                        augmented if augment else original,
                        classification=label,
                        incident_category=category or None,
                        top_k=agent_settings.agent_retrieval_top_k,
                        threshold=agent_settings.agent_retrieval_threshold,
                    )
                    ids = list(dict.fromkeys(hit.article_id for hit in result.hits))
                    rank = next(
                        (i for i, article in enumerate(ids, 1) if article in expected), None
                    )
                    scored.append(
                        {
                            "incident_id": row["incident_id"],
                            "answerable": row["is_answerable"],
                            "returned_article_ids": ids,
                            "hit_at_1": bool(ids and ids[0] in expected),
                            "hit_at_5": bool(expected.intersection(ids)),
                            "primary_recall": len(set(primary).intersection(ids)) / len(primary)
                            if primary
                            else 0,
                            "reciprocal_rank": 1 / rank if rank else 0,
                            "forbidden_hit": bool(
                                set(row["forbidden_article_ids"]).intersection(ids)
                            ),
                            "sufficient": result.sufficient,
                            "best_relevance": result.best_relevance,
                            "rewrite_changed": augment and augmented != original,
                            "latency_ms": result.latency_ms + (rewrite_ms if augment else 0),
                        }
                    )
                name = mode.value + ("_rewrite" if augment else "_original")
                report["variants"][name] = {"summary": summarize(scored), "cases": scored}
        final_digest, _ = snapshot(client, collection)
        report["payload_snapshot_unchanged"] = digest == final_digest
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({k: v["summary"] for k, v in report["variants"].items()}, indent=2))
        if not report["payload_snapshot_unchanged"]:
            raise RuntimeError(
                "Payload snapshot changed during evaluation; do not compare these variants"
            )
    finally:
        client.close()
        if previous_mode is None:
            os.environ.pop("RETRIEVAL_MODE", None)
        else:
            os.environ["RETRIEVAL_MODE"] = previous_mode
        get_retrieval_settings.cache_clear()


if __name__ == "__main__":
    main()
