"""Stage A acceptance runner: the 87 answerable turns against a scratch collection.

Evaluates the actual shared retrieval path — never production, never a
generated answer. Per turn it runs ``QdrantRetriever.search`` on the dataset's
``standalone_input``, records the retrieved chunk texts, and hands them plus
the dataset reference to the selected judge layer:

- ``fake`` (default, no credentials): a deterministic stub for plumbing tests;
  its scores are marked non-accepting.
- ``litellm``: RAGAS native ``context_precision`` / ``context_recall`` with the
  pinned judge via the LiteLLM proxy (OpenAI-compatible client).

Acceptance stays *pending* unless the metric policy is signed (``status:
agreed`` with non-null floors) AND the live judge is used. Deterministic
integrity checks always run: every answerable turn must be accounted for,
and any ``must_not_retrieve`` label among the retrieved sections is a
zero-tolerance violation that fails the run regardless of metric averages.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


def _load_adapters():
    """Load ``data/structured-io/adapters.py`` by path (not an importable package)."""
    import importlib.util

    adapters_path = REPO_ROOT / "data" / "structured-io" / "adapters.py"
    spec = importlib.util.spec_from_file_location("stage_a_adapters", adapters_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


from agent.retrieval import QdrantRetriever  # noqa: E402
from app.models.knowledge import Classification  # noqa: E402
from app.retrieval.embedding import FastEmbedEngine  # noqa: E402
from app.retrieval.ingest import ingest_articles  # noqa: E402
from app.retrieval.manual.integration import load_manual_publication  # noqa: E402
from app.retrieval.sources import LocalJSONSource  # noqa: E402

adapters = _load_adapters()
PRODUCTION_COLLECTION = "incident_knowledge_base"
DEFAULT_THRESHOLD = 0.55
DEFAULT_TOP_K = 8


class Judge(Protocol):
    """Scores one row's contexts against its reference; never generates answers."""

    name: str

    def score(self, row: dict, contexts: list[str]) -> dict[str, float | None]: ...


class FakeJudge:
    """Deterministic plumbing stub; its numbers are never acceptance evidence."""

    name = "fake"

    def __init__(self) -> None:
        self.invoked: list[str] = []

    def score(self, row: dict, contexts: list[str]) -> dict[str, float | None]:
        self.invoked.append(row["turn_id"])
        return {"context_recall": 0.5, "context_precision": 0.5}


class JudgeError(Exception):
    pass


class RagasJudge:
    """RAGAS native context metrics via the LiteLLM proxy (pinned in policy)."""

    name = "ragas"

    def __init__(self, policy: dict) -> None:
        try:
            import sys
            import types

            import langchain_community.llms

            class VertexAI:
                pass

            langchain_community.llms.VertexAI = VertexAI

            import langchain_community.chat_models

            cv = types.ModuleType("langchain_community.chat_models.vertexai")

            class ChatVertexAI:
                pass

            cv.ChatVertexAI = ChatVertexAI
            sys.modules["langchain_community.chat_models.vertexai"] = cv
            langchain_community.chat_models.vertexai = cv

            from langchain_openai import ChatOpenAI
            from ragas import evaluate
            from ragas.llms import LangchainLLMWrapper
            from ragas.metrics import ContextPrecision, ContextRecall
        except ImportError as exc:  # pragma: no cover - environment guard
            raise JudgeError("ragas is not installed; run `uv sync --group eval` first") from exc
        import os

        from dotenv import load_dotenv

        load_dotenv()

        base_url = os.environ.get("LITELLM_BASE_URL")
        api_key = os.environ.get("LITELLM_API_KEY")
        if not (base_url and api_key and policy.get("judge_model")):
            raise JudgeError(
                "live judging needs LITELLM_BASE_URL, LITELLM_API_KEY and a "
                "policy judge_model; refusing to fall back to any default judge"
            )
        if not base_url.endswith("/v1"):
            base_url = f"{base_url.rstrip('/')}/v1"

        self._evaluate = evaluate
        self._metrics = [ContextPrecision(), ContextRecall()]
        self._llm = LangchainLLMWrapper(
            ChatOpenAI(model=policy["judge_model"], base_url=base_url, api_key=api_key)
        )

    def score(self, row: dict, contexts: list[str]) -> dict[str, float | None]:
        from datasets import Dataset

        ds = Dataset.from_dict(
            {
                "user_input": [row["user_input"]],
                "retrieved_contexts": [contexts],
                "reference": [row["reference"]],
            }
        )
        result = self._evaluate(ds, metrics=self._metrics, llm=self._llm, raise_exceptions=True)
        row_result = result.to_pandas().iloc[0]
        return {
            "context_precision": _as_score(row_result.get("context_precision")),
            "context_recall": _as_score(row_result.get("context_recall")),
        }


def _as_score(value: Any) -> float | None:
    try:
        return None if value is None else round(float(value), 4)
    except (TypeError, ValueError):
        return None


def section_reverse_map(manifest) -> dict[str, list[str]]:
    """KB article_id → dataset section labels, from the reviewed manifest."""
    mapping: dict[str, list[str]] = {}
    for unit in manifest.units:
        if unit.article_number and unit.version:
            mapping[f"{unit.article_number}-v{unit.version}"] = list(unit.source_sections)
    return mapping


def collect_contexts(
    rows: list[dict],
    retriever: QdrantRetriever,
    reverse: dict[str, list[str]],
    *,
    top_k: int,
    threshold: float,
    limit: int | None = None,
) -> list[dict]:
    """Run real retrieval per row; records contexts and integrity, no judging."""
    records = []
    for row in rows[:limit] if limit else rows:
        started = time.perf_counter()
        record: dict[str, Any] = {
            "turn_id": row["turn_id"],
            "user_input": row["user_input"],
            "contexts": [],
            "section_labels": [],
            "source_ids": [],
            "latency_ms": None,
            "error": None,
        }
        try:
            # OTHER means no category-scoped pass: the wide, security-filtered
            # search is the path every label may lawfully use.
            result = retriever.search(
                row["user_input"],
                classification=Classification.OTHER,
                top_k=top_k,
                threshold=threshold,
            )
            record["contexts"] = [h.text for h in result.hits]
            record["source_ids"] = [h.article_id for h in result.hits]
            labels: list[str] = []
            for hit in result.hits:
                labels.extend(reverse.get(hit.article_id, []))
            record["section_labels"] = sorted(set(labels))
            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        except Exception as exc:  # noqa: BLE001 - recorded, never counted as a pass
            record["error"] = f"{type(exc).__name__}: {exc}"
        records.append(record)
    return records


def apply_integrity(rows: list[dict], records: list[dict]) -> list[str]:
    """Zero-tolerance checks; returns violation descriptions."""
    by_id = {r["turn_id"]: r for r in records}
    violations = []
    forbidden = {r["turn_id"]: set(r["must_not_retrieve"]) for r in rows if r["must_not_retrieve"]}
    for turn_id, labels in forbidden.items():
        record = by_id.get(turn_id)
        if record is None:
            violations.append(f"{turn_id}: row missing from the run entirely")
            continue
        hit = labels & set(record["section_labels"])
        if hit:
            violations.append(
                f"{turn_id}: forbidden section(s) {sorted(hit)} retrieved "
                f"via {record['source_ids']}"
            )
    missing = [r["turn_id"] for r in rows if r["turn_id"] not in by_id]
    if missing:
        violations.append(f"turns missing from the run: {missing}")
    return violations


def aggregate_by_capability(rows: list[dict], records: list[dict]) -> dict:
    """Mean metric score per capability slice over scored rows."""
    scores: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_id = {r["turn_id"]: r for r in records}
    for row in rows:
        record = by_id.get(row["turn_id"])
        if not record or record.get("scores") is None:
            continue
        for cap in row["requires"] or ["uncapability"]:
            for metric, value in record["scores"].items():
                if value is not None:
                    scores[cap][metric].append(value)
    return {
        cap: {m: round(sum(v) / len(v), 4) for m, v in metrics.items()}
        for cap, metrics in sorted(scores.items())
    }


def run(
    *,
    qdrant_url: str,
    collection: str,
    judge: Judge,
    client_factory: Callable[[], Any],
    engine_factory: Callable[[], Any] | None = None,
    limit: int | None = None,
    top_k: int = DEFAULT_TOP_K,
    threshold: float = DEFAULT_THRESHOLD,
    manifest_path: Path | None = None,
    corpus_path: Path | None = None,
    sections_path: Path | None = None,
    skip_ingest: bool = False,
) -> dict:
    """One Stage A run; pure-ish so tests can inject fakes end to end."""
    policy = adapters.load_metric_policy()
    adapters.validate_dataset()  # contract check; rows load their own data
    rows = adapters.retrieval_only_rows()
    manifest_path = manifest_path or REPO_ROOT / "data" / "corpus" / "manual_kb_manifest.json"
    corpus_path = corpus_path or REPO_ROOT / "data" / "corpus" / "barq_articles.json"
    sections_path = sections_path or REPO_ROOT / "data" / "corpus" / "manual_sections.json"

    client = client_factory()
    baseline = LocalJSONSource(corpus_path).load_articles()
    publication = load_manual_publication(manifest_path, sections_path, corpus_path)
    engine_factory = engine_factory or FastEmbedEngine
    if not skip_ingest:
        ingest_articles(
            articles=[*baseline, *publication.articles],
            client=client,
            collection_name=collection,
            embedding_engine=engine_factory(),
            article_provenance=publication.provenance,
        )
    retriever = QdrantRetriever(
        lambda: client,
        engine_factory,
        collection_name=collection,
    )

    manifest = load_manifest_for(manifest_path)
    records = collect_contexts(
        rows,
        retriever,
        section_reverse_map(manifest),
        top_k=top_k,
        threshold=threshold,
        limit=limit,
    )
    rows_by_id = {r["turn_id"]: r for r in rows}
    for record in records:
        if record["error"] is None and record["contexts"]:
            try:
                record["scores"] = judge.score(rows_by_id[record["turn_id"]], record["contexts"])
            except Exception as exc:  # noqa: BLE001 - a judge failure is a failure
                record["scores"] = None
                record["judge_error"] = f"{type(exc).__name__}: {exc}"
        else:
            record["scores"] = None

    violations = apply_integrity(rows, records)
    agreed = policy["status"] == "agreed" and all(
        m.get("floor") is not None for m in policy["metrics"].values()
    )
    acceptance = "agreed" if agreed and judge.name == "ragas" else "pending"
    report = {
        "acceptance": acceptance,
        "judge": judge.name,
        "policy_status": policy["status"],
        "thresholds_agreed": policy["thresholds_agreed"],
        "retrieval": {
            "top_k": top_k,
            "threshold": threshold,
            "url": qdrant_url,
            "collection": collection,
        },
        "accounting": {
            "dataset_turns": 100,
            "answerable_retrieval_rows": len(rows),
            "evaluated": sum(1 for r in records if r["scores"]),
            "retrieval_errors": sum(1 for r in records if r["error"]),
            "judge_errors": sum(1 for r in records if r.get("judge_error")),
            "conversation_pending": 100 - len(rows),
        },
        "integrity_violations": violations,
        "capability_means": aggregate_by_capability(rows, records),
        "rows": records,
    }
    return report


def load_manifest_for(path: Path):
    from app.retrieval.manual.manifest import load_manifest

    return load_manifest(path)


def _qdrant_client(url: str):
    from qdrant_client import QdrantClient

    return QdrantClient(url=url)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--qdrant-url",
        required=True,
        help="Explicit scratch Qdrant endpoint; there is deliberately no default.",
    )
    parser.add_argument(
        "--collection",
        required=True,
        help="Scratch collection name; the production collection name is rejected.",
    )
    parser.add_argument("--judge", choices=["fake", "litellm"], default="fake")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--limit", type=int, default=None, help="Smoke-run the first N rows.")
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Skip chunking and upserting articles if the scratch collection is already populated.",
    )
    parser.add_argument("--report", type=Path, default=REPO_ROOT / "eval" / "stage_a_report.json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.collection == PRODUCTION_COLLECTION:
        print(
            f"Error: {PRODUCTION_COLLECTION!r} is the production collection; "
            "Stage A runs against an explicit scratch collection only.",
            file=sys.stderr,
        )
        return 1

    policy = adapters.load_metric_policy()
    judge = FakeJudge() if args.judge == "fake" else RagasJudge(policy)
    report = run(
        qdrant_url=args.qdrant_url,
        collection=args.collection,
        judge=judge,
        client_factory=lambda: _qdrant_client(args.qdrant_url),
        limit=args.limit,
        top_k=args.top_k,
        threshold=args.threshold,
        skip_ingest=args.skip_ingest,
    )
    report["policy"] = policy
    args.report.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    acc = report["accounting"]
    print(
        f"Stage A ({judge.name}, acceptance={report['acceptance']}): "
        f"{acc['evaluated']}/{acc['answerable_retrieval_rows']} evaluated, "
        f"{acc['conversation_pending']} pending Stage B, "
        f"{len(report['integrity_violations'])} integrity violations"
    )
    for violation in report["integrity_violations"]:
        print(f"  VIOLATION: {violation}", file=sys.stderr)
    if report["integrity_violations"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
