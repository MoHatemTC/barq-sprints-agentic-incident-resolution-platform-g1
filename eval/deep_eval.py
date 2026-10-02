"""DeepEval judge over the 100-turn dataset with a grounded-QA run().

run(question, history) — the callback the dataset's adapter expects:
  1. hybrid+rank-fusion retrieval on manual_semantic_sections (top-k), the
     Stage A path already measured at 0.90 recall,
  2. one Gemini call through the Sprints LiteLLM proxy (OpenAI-compatible)
     with a grounding prompt: answer only from the retrieved sections, cite
     section numbers, refuse or clarify when they don't cover the question.

The judge is Gemini via DeepEval's LiteLLMModel — no OpenAI key, no Confident
account. Metrics: FaithfulnessMetric (answer vs retrieved context) plus GEval
rubrics taken verbatim from the dataset's metric_suite; refuse/clarify turns
are judged with a refusal rubric instead of the answer rubrics.

Results are written as a JSON artifact for eval/generate_stage_b_report.py.

Run with:
  .venv/bin/python eval/deep_eval.py --limit 5        # smoke
  .venv/bin/python eval/deep_eval.py                  # full 100 turns
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import openai

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from agent.config import AgentSettings  # noqa: E402
from app.core.config import RetrievalMode, get_retrieval_settings  # noqa: E402

QDRANT_URL = get_retrieval_settings().qdrant_url
AGENT_SETTINGS = AgentSettings()

from qdrant_client import QdrantClient  # noqa: E402

from app.retrieval.embedding import FastEmbedEngine  # noqa: E402

# The corpus adapter opens barq_rag_eval_dataset.json relative to the CWD.
os.chdir(REPO / "data" / "corpus")
sys.path.insert(0, str(REPO / "data" / "corpus"))
import adapters as A  # noqa: E402

os.chdir(REPO)
from deepeval.metrics import FaithfulnessMetric, GEval  # noqa: E402
from deepeval.models import DeepEvalBaseLLM  # noqa: E402
from deepeval.models.llms.utils import trim_and_load_json  # noqa: E402
from deepeval.test_case import LLMTestCase  # noqa: E402
from deepeval.test_case import SingleTurnParams as P  # noqa: E402
from scripts.smoke_eval_retrieval import search  # noqa: E402

GROUNDING_SYSTEM_PROMPT = """\
You answer questions about the BARQ Systems IT Service Desk Operations Manual.

Rules:
1. Answer ONLY from the manual sections provided in the prompt. Never use
   outside knowledge.
2. Cite the section numbers you used, like (§3.4).
3. If the provided sections do not cover the question, state that the manual
   does not cover it — optionally naming the nearest relevant section — and
   do NOT invent an answer.
4. If the question is ambiguous, ask one clarifying question instead of
   guessing.
5. Be concise: the answer, the citations, nothing else."""

_REFUSAL_RE = re.compile(
    r"does not cover|not covered|cannot answer|no (?:information|policy|section)|"
    r"manual (?:doesn'?t|does not)|clarify",
    re.IGNORECASE,
)

REFUSAL_RUBRIC = (
    "The expected behaviour for this turn is a REFUSAL or a clarifying "
    "question. The answer must decline to give the requested information, "
    "must not fabricate a policy, number, date or procedure, and may name "
    "the nearest relevant manual section. Answering the question with "
    "invented content is a failure of the highest weight."
)

JUDGE_PARAMS = [P.INPUT, P.ACTUAL_OUTPUT, P.EXPECTED_OUTPUT, P.RETRIEVAL_CONTEXT]

# USD spend captured from the LiteLLM proxy's x-litellm-response-cost header.
SPEND = {"usd": 0.0, "calls": 0}
_SPEND_LOCK = threading.Lock()


def record_cost(headers) -> None:
    raw = headers.get("x-litellm-response-cost") if headers is not None else None
    if raw is None:
        return
    try:
        with _SPEND_LOCK:
            SPEND["usd"] += float(raw)
            SPEND["calls"] += 1
    except (TypeError, ValueError):
        pass


def build_context_block(retrieved: list[dict]) -> str:
    parts = []
    for h in retrieved:
        parts.append(f"§{h['section']} {h['title']}\n{h['chunk_text']}")
    return "\n\n---\n\n".join(parts)


def build_history_block(history: list[dict]) -> str:
    if not history:
        return ""
    lines = ["Earlier in this conversation:"]
    for turn in history:
        lines.append(f"- user asked: {turn['question']}")
        lines.append(f"  you answered: {turn['answer'][:300]}")
    return "\n".join(lines) + "\n\n"


def make_run(
    qdrant, gen_client, collection: str, engine: FastEmbedEngine, gen_model: str, top_k: int
):
    """Return run(question, history) -> (answer, retrieved) for the adapter."""
    retrieval_lock = threading.Lock()  # serialize shared ONNX inference

    def generate_with_retry(messages: list[dict]) -> str:
        # 429s (rate limits / budget caps) are transient-or-fatal; retry the
        # former briefly so one blip doesn't kill a 20-minute run.
        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                raw = gen_client.chat.completions.with_raw_response.create(
                    model=gen_model,
                    temperature=0,
                    messages=messages,
                )
                record_cost(raw.headers)
                return raw.parse().choices[0].message.content.strip()
            except openai.RateLimitError as exc:
                last_exc = exc
                if "budget_exceeded" in str(exc):
                    raise SystemExit(
                        "LiteLLM key budget exhausted — raise the key budget on the "
                        "proxy, then rerun (completed turns are checkpointed and "
                        "skipped)."
                    ) from exc
                time.sleep(2**attempt * 5)
        raise RuntimeError(f"generation failed after retries: {last_exc}")

    def run(question: str, history: list[dict]) -> tuple[str, list[dict]]:
        with retrieval_lock:
            hits = search(
                qdrant, engine, RetrievalMode.HYBRID_RERANKED, collection, question, top_k
            )
        retrieved = [
            {
                "section": h.article_number,
                "title": h.title,
                "chunk_text": h.chunk_text,
                "score": round(h.score, 4),
            }
            for h in hits
        ]
        user = (
            build_history_block(history)
            + "MANUAL SECTIONS:\n\n"
            + build_context_block(retrieved)
            + f"\n\nQUESTION: {question}"
        )
        answer = generate_with_retry(
            [
                {"role": "system", "content": GROUNDING_SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ]
        )
        return answer, retrieved

    return run


class GeminiProxyJudge(DeepEvalBaseLLM):
    """DeepEval judge model speaking to the Sprints LiteLLM proxy.

    DeepEval's own LiteLLMModel strips the "gemini/" provider prefix
    (parse_model_name), which the proxy then rejects ("team can only access
    models=['gemini/*']"). This custom model calls the proxy through the same
    OpenAI-compatible path as the generator, keeping the full model name.
    """

    def __init__(self, model: str, client) -> None:
        self._model_name = model
        self._client = client

    def load_model(self):
        return self._client

    def generate(self, prompt: str, schema=None):
        kwargs = {"response_format": {"type": "json_object"}} if schema else {}
        raw = self._client.chat.completions.with_raw_response.create(
            model=self._model_name,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        record_cost(raw.headers)
        text = raw.parse().choices[0].message.content
        if schema is not None:
            return schema(**json.loads(trim_and_load_json(text)))
        return text

    async def a_generate(self, prompt: str, schema=None):
        import asyncio

        return await asyncio.to_thread(self.generate, prompt, schema)

    def get_model_name(self) -> str:
        return self._model_name


def judge_model(args, gen_client) -> GeminiProxyJudge:
    api_key = AGENT_SETTINGS.litellm_api_key
    if api_key is None or not api_key.get_secret_value():
        raise SystemExit("LITELLM_API_KEY is not configured (needed for the judge)")
    return GeminiProxyJudge(model=args.judge_model, client=gen_client)


def metrics_for_turn(t: dict, judge, threshold: float) -> dict[str, GEval]:
    """Metric set for one turn: answer rubrics vs the refusal rubric."""
    metrics: dict[str, GEval] = {}
    if t["expected_behaviour"] == "answer":
        metrics["faithfulness"] = FaithfulnessMetric(threshold=threshold, model=judge)
        for name, criteria in A.DATA["metric_suite"]["geval_global_rubrics"].items():
            if name == "refusal_quality":
                continue  # answer turns are not judged on refusing
            metrics[name] = GEval(
                name=name,
                criteria=criteria,
                evaluation_params=JUDGE_PARAMS,
                threshold=threshold,
                model=judge,
            )
        if t.get("geval_criteria"):
            metrics["turn_rubric"] = GEval(
                name="turn_rubric",
                criteria=t["geval_criteria"],
                evaluation_params=JUDGE_PARAMS,
                threshold=threshold,
                model=judge,
            )
    else:
        metrics["refusal_quality"] = GEval(
            name="refusal_quality",
            criteria=(t.get("geval_criteria") or REFUSAL_RUBRIC),
            evaluation_params=JUDGE_PARAMS,
            threshold=threshold,
            model=judge,
        )
    return metrics


def judge_turn(t: dict, run, judge, threshold: float, index: int, history: list[dict]) -> dict:
    """Retrieve + answer + judge one turn. Metrics run concurrently."""
    answer, retrieved = run(t["standalone_input"], history)
    sections = [r["section"] for r in retrieved]
    refusal = bool(_REFUSAL_RE.search(answer))

    metrics = metrics_for_turn(t, judge, threshold)
    case = LLMTestCase(
        input=t["standalone_input"],
        actual_output=answer,
        expected_output=t["reference"],
        retrieval_context=[build_context_block(retrieved)],
    )
    scores: dict[str, dict] = {}

    def _measure(name: str, metric) -> None:
        last_exc: Exception | None = None
        for attempt in range(4):  # transient 429s / proxy blips
            try:
                metric.measure(case, _in_component=True)
                scores[name] = {
                    "score": round(float(metric.score), 3),
                    "success": bool(metric.is_successful()),
                    "reason": str(metric.reason)[:400],
                }
                return
            except Exception as exc:
                last_exc = exc
                if "budget_exceeded" in str(exc):
                    raise
                time.sleep(2**attempt * 5)
        scores[name] = {
            "score": None,
            "success": False,
            "reason": f"error after retries: {last_exc}",
        }

    with ThreadPoolExecutor(max_workers=len(metrics)) as pool:
        futures = [pool.submit(_measure, name, metric) for name, metric in metrics.items()]
        for f in as_completed(futures):
            f.result()

    all_ok = all(s["success"] for s in scores.values()) if scores else False
    behaviour_ok = refusal == (t["expected_behaviour"] != "answer")
    return {
        "_index": index,
        "turn_id": t["turn_id"],
        "behaviour": t["expected_behaviour"],
        "difficulty": t["difficulty"],
        "requires": t["requires"],
        "query": t["standalone_input"],
        "expected_sections": t["expected_sections"],
        "retrieved_sections": sections,
        "answer": answer,
        "refusal_detected": refusal,
        "behaviour_ok": behaviour_ok,
        "metrics": scores,
        "verdict": "PASS" if (all_ok and behaviour_ok) else "FAIL",
    }


def eval_session(
    session_turns: list[dict], run, judge, threshold: float, first_index: int, on_turn
) -> list[dict]:
    """One conversation: turns strictly sequential so coreference history
    builds correctly; each turn's metrics run concurrently inside judge_turn."""
    history: list[dict] = []
    rows = []
    for offset, t in enumerate(session_turns):
        row = judge_turn(t, run, judge, threshold, first_index + offset, history)
        history.append({"question": t["standalone_input"], "answer": row["answer"]})
        rows.append(row)
        on_turn(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default="manual_semantic_sections")
    parser.add_argument("--url", default=QDRANT_URL)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=0, help="first N turns only (0 = all)")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=6,
        help="sessions evaluated in parallel (each stays internally sequential)",
    )
    parser.add_argument("--judge-model", default=AGENT_SETTINGS.agent_llm_model)
    parser.add_argument("--gen-model", default=AGENT_SETTINGS.agent_llm_model)
    parser.add_argument("--threshold", type=float, default=0.7)
    parser.add_argument("--output", default="eval/deepeval_results.json")
    args = parser.parse_args()

    sessions = A.DATA["sessions"]
    if args.limit:
        limited, taken = [], 0
        for s in sessions:
            if taken >= args.limit:
                break
            chunk = s["turns"][: args.limit - taken]
            limited.append({**s, "turns": chunk})
            taken += len(chunk)
        sessions = limited
    total_turns = sum(len(s["turns"]) for s in sessions)

    if (
        AGENT_SETTINGS.litellm_api_key is None
        or not AGENT_SETTINGS.litellm_api_key.get_secret_value()
    ):
        raise SystemExit("LITELLM_API_KEY is not configured")
    gen_client = openai.OpenAI(
        base_url=AGENT_SETTINGS.litellm_base_url,
        api_key=AGENT_SETTINGS.litellm_api_key.get_secret_value(),
    )
    judge = judge_model(args, gen_client)
    engine = FastEmbedEngine()
    client = QdrantClient(url=args.url)
    run = make_run(client, gen_client, args.collection, engine, args.gen_model, args.top_k)

    print(
        f"turns: {total_turns} in {len(sessions)} sessions | {args.concurrency} sessions "
        f"in parallel | gen: {args.gen_model} | judge: {args.judge_model} | "
        f"threshold: {args.threshold}\n",
        flush=True,
    )

    output = Path(args.output) if Path(args.output).is_absolute() else REPO / args.output

    # Resume support: turns already present in a previous artifact are skipped,
    # so a budget-capped or crashed run continues where it stopped.
    prev_rows: list[dict] = []
    if output.exists():
        try:
            prev_rows = json.loads(output.read_text(encoding="utf-8"))["turns"]
        except (json.JSONDecodeError, KeyError):
            prev_rows = []
    done_ids = {r["turn_id"] for r in prev_rows}
    if done_ids:
        print(f"resuming: {len(done_ids)} turns already completed in {output.name}\n", flush=True)

    print_lock = threading.Lock()
    done_count = [0]
    new_rows: list[dict] = []
    started = time.perf_counter()

    def _natural_key(row: dict) -> tuple:
        m = re.match(r"S(\d+)-T(\d+)", row["turn_id"])
        return (int(m.group(1)), int(m.group(2))) if m else (999, 999)

    def write_checkpoint() -> None:
        merged = sorted(prev_rows + new_rows, key=_natural_key)
        output.write_text(
            json.dumps(
                {
                    "config": {
                        "collection": args.collection,
                        "gen_model": args.gen_model,
                        "judge_model": args.judge_model,
                        "threshold": args.threshold,
                        "top_k": args.top_k,
                    },
                    "turns": merged,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def on_turn(row: dict) -> None:
        with print_lock:
            new_rows.append(row)
            write_checkpoint()
            done_count[0] += 1
            flag = "" if row["verdict"] == "PASS" else "  <-- FAIL"
            scores = ", ".join(f"{k}={v['score']}" for k, v in row["metrics"].items())
            print(
                f"  [{row['turn_id']}] {row['behaviour']:7s} {scores} "
                f"refused={row['refusal_detected']}{flag}",
                flush=True,
            )
            print(
                f"    ... {done_count[0] + len(prev_rows)}/{total_turns} turns "
                f"({time.perf_counter() - started:.0f}s)",
                flush=True,
            )

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = []
        for s in sessions:
            remaining = [t for t in s["turns"] if t["turn_id"] not in done_ids]
            if remaining:
                futures.append(
                    pool.submit(eval_session, remaining, run, judge, args.threshold, 0, on_turn)
                )
        for f in as_completed(futures):
            rows.extend(f.result())

    all_rows = sorted(prev_rows + rows, key=_natural_key)

    # ---- aggregate ---------------------------------------------------------
    rows = all_rows
    metric_names = sorted({k for r in rows for k in r["metrics"]})
    summary = {
        "turns": len(rows),
        "verdicts": dict(collections.Counter(r["verdict"] for r in rows)),
        "per_metric": {
            name: {
                "avg_score": round(
                    sum(
                        r["metrics"][name]["score"]
                        for r in rows
                        if r["metrics"].get(name, {}).get("score") is not None
                    )
                    / max(
                        1,
                        sum(1 for r in rows if r["metrics"].get(name, {}).get("score") is not None),
                    ),
                    3,
                ),
                "pass_rate": round(
                    sum(1 for r in rows if r["metrics"].get(name, {}).get("success"))
                    / max(1, sum(1 for r in rows if name in r["metrics"])),
                    3,
                ),
            }
            for name in metric_names
        },
        "behaviour_accuracy": round(sum(1 for r in rows if r["behaviour_ok"]) / len(rows), 3),
    }
    passed_rows = [{"turn_id": r["turn_id"], "passed": r["verdict"] == "PASS"} for r in rows]
    summary["capability_pass_rates"] = A.slice_report(passed_rows)

    output.write_text(
        json.dumps(
            {
                "config": {
                    "collection": args.collection,
                    "gen_model": args.gen_model,
                    "judge_model": args.judge_model,
                    "threshold": args.threshold,
                    "top_k": args.top_k,
                    "spend_this_run_usd": round(SPEND["usd"], 4),
                    "llm_calls_this_run": SPEND["calls"],
                },
                "summary": summary,
                "turns": rows,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    verdicts_json = json.dumps(summary["verdicts"])
    print(f"\nsummary: {verdicts_json} | behaviour accuracy {summary['behaviour_accuracy']}")
    for name, st in summary["per_metric"].items():
        print(f"  {name:18s} avg {st['avg_score']:.3f}  pass {st['pass_rate']:.0%}")
    print(f"spend this run: ${SPEND['usd']:.4f} across {SPEND['calls']} billed calls")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
