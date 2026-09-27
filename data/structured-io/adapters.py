"""
Load barq_rag_eval_dataset.json into DeepEval or RAGAS.

The dataset is framework-neutral. This file is the thin layer that turns it into
whatever your evaluation harness expects, plus a per-turn retrieval scorer that
does not need an LLM judge at all.

    python adapters.py --list-capabilities
    python adapters.py --slice image_ocr
"""

from __future__ import annotations

import argparse
import collections
import json
from collections.abc import Callable, Iterable
from pathlib import Path

_DATA_PATH = Path(__file__).parent / "barq_rag_eval_dataset.json"
DATA = json.loads(_DATA_PATH.read_text(encoding="utf-8"))


# ──────────────────────────────────────────────────────────── selection
def turns(where: Callable[[dict], bool] | None = None) -> list[dict]:
    out = []
    for s in DATA["sessions"]:
        for t in s["turns"]:
            t = {**t, "_session": {k: v for k, v in s.items() if k != "turns"}}
            if where is None or where(t):
                out.append(t)
    return out


def by_capability(cap: str) -> list[dict]:
    return turns(lambda t: cap in t["requires"])


def answerable() -> list[dict]:
    return turns(lambda t: t["expected_behaviour"] == "answer")


def negatives() -> list[dict]:
    return turns(lambda t: t["expected_behaviour"] in ("refuse", "clarify"))


# ──────────────────────────────────────────────────────────── DeepEval
def to_deepeval_cases(run, standalone: bool = False):
    """run(question:str, history:list[dict]) -> (answer:str, retrieved:list[str])"""
    from deepeval.test_case import LLMTestCase

    cases = []
    for s in DATA["sessions"]:
        history = []
        for t in s["turns"]:
            q = t["standalone_input"] if standalone else t["input"]
            answer, retrieved = run(q, history)
            history += [
                {"role": "user", "content": t["input"]},
                {"role": "assistant", "content": answer},
            ]
            cases.append(
                LLMTestCase(
                    input=q,
                    actual_output=answer,
                    expected_output=t["reference"],
                    retrieval_context=retrieved,
                    context=t["reference_contexts"] or None,
                    additional_metadata={
                        "turn_id": t["turn_id"],
                        "session_id": s["session_id"],
                        "behaviour": t["expected_behaviour"],
                        "difficulty": t["difficulty"],
                        "requires": t["requires"],
                        "tags": t["tags"],
                        "expected_sections": t["expected_sections"],
                        "must_not_retrieve": t["must_not_retrieve"],
                        "geval_criteria": t["geval_criteria"],
                    },
                )
            )
    return cases


def to_deepeval_conversations(run):
    """One ConversationalTestCase per session, for multi-turn metrics."""
    from deepeval.test_case import ConversationalTestCase, Turn

    convos = []
    for s in DATA["sessions"]:
        history, turns_ = [], []
        for t in s["turns"]:
            answer, _ = run(t["input"], history)
            history += [
                {"role": "user", "content": t["input"]},
                {"role": "assistant", "content": answer},
            ]
            turns_ += [
                Turn(role="user", content=t["input"]),
                Turn(role="assistant", content=answer),
            ]
        convos.append(
            ConversationalTestCase(
                turns=turns_,
                scenario=s["scenario"],
                expected_outcome=s["expected_outcome"],
                user_description=s["user_description"],
                additional_metadata={"session_id": s["session_id"], "title": s["title"]},
            )
        )
    return convos


def geval_metrics():
    """One GEval metric per global rubric, plus per-turn rubrics where present."""
    from deepeval.metrics import GEval
    from deepeval.test_case import LLMTestCaseParams as P

    rub = DATA["metric_suite"]["geval_global_rubrics"]
    params = [P.INPUT, P.ACTUAL_OUTPUT, P.EXPECTED_OUTPUT, P.RETRIEVAL_CONTEXT]
    return {
        name: GEval(name=name, criteria=text, evaluation_params=params, threshold=0.7)
        for name, text in rub.items()
    }


# ──────────────────────────────────────────────────────────── RAGAS
def to_ragas(run, standalone: bool = True):
    """Returns a list of dicts ready for ragas.EvaluationDataset.from_list."""
    rows = []
    for s in DATA["sessions"]:
        history = []
        for t in s["turns"]:
            q = t["standalone_input"] if standalone else t["input"]
            answer, retrieved = run(q, history)
            history += [
                {"role": "user", "content": t["input"]},
                {"role": "assistant", "content": answer},
            ]
            rows.append(
                {
                    "user_input": q,
                    "response": answer,
                    "retrieved_contexts": retrieved,
                    "reference": t["reference"],
                    "reference_contexts": t["reference_contexts"],
                }
            )
    return rows


def to_ragas_multiturn(run):
    """Multi-turn form: one sample per session with the full message list."""
    from ragas.messages import AIMessage, HumanMessage

    samples = []
    for s in DATA["sessions"]:
        history, msgs = [], []
        for t in s["turns"]:
            answer, _ = run(t["input"], history)
            history += [
                {"role": "user", "content": t["input"]},
                {"role": "assistant", "content": answer},
            ]
            msgs += [HumanMessage(content=t["input"]), AIMessage(content=answer)]
        samples.append(
            {
                "user_input": msgs,
                "reference": s["expected_outcome"],
                "metadata": {"session_id": s["session_id"]},
            }
        )
    return samples


# ──────────────────────────────────────────────────── retrieval scoring
def score_retrieval(retrieved_sections: Iterable[str], t: dict) -> dict:
    """Deterministic, no judge required. Feed it the section ids your retriever returned."""
    got = set(retrieved_sections)
    want = set(t["expected_sections"]) - {"—"}
    forbidden = set(t["must_not_retrieve"])
    hit = want & got
    return {
        "turn_id": t["turn_id"],
        "recall": len(hit) / len(want) if want else None,
        "precision": len(hit) / len(got) if got else 0.0,
        "top_k_contains_all": want.issubset(got) if want else None,
        "forbidden_retrieved": sorted(forbidden & got),
        "clean": not (forbidden & got),
    }


# ──────────────────────────────────────────────────────────── reporting
def slice_report(results: list[dict]):
    """results: [{turn_id, passed: bool}, ...]  → pass rate per capability tag."""
    index = {t["turn_id"]: t for t in turns()}
    agg = collections.defaultdict(lambda: [0, 0])
    for r in results:
        t = index.get(r["turn_id"])
        if not t:
            continue
        for cap in t["requires"] + [
            f"difficulty:{t['difficulty']}",
            f"behaviour:{t['expected_behaviour']}",
        ]:
            agg[cap][1] += 1
            agg[cap][0] += bool(r["passed"])
    return {
        k: {"passed": v[0], "total": v[1], "rate": round(v[0] / v[1], 3)}
        for k, v in sorted(agg.items())
    }


# ───────────────────────────────── Stage A contract (manual-KB integration)
# The 100-turn dataset is the sole Stage A gate; the legacy 33-case suite is
# retired. This section validates the dataset, declares per-turn layer
# applicability, and provides a retrieval-only loader for RAGAS: it never
# generates answers — the runner attaches real retrieved contexts.

DATASET_CONTRACT = {"sessions": 20, "turns": 100}
BEHAVIOUR_CONTRACT = {"answer": 87, "refuse": 12, "clarify": 1}
RUBRIC_BEARING_TURNS = 35
CONVERSATION_BEHAVIOURS = frozenset({"refuse", "clarify"})
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = REPO_ROOT / "eval" / "manual_stage_a_policy.json"
_POLICY_REQUIRED_KEYS = (
    "framework",
    "judge_model",
    "context_budget",
    "metrics",
    "aggregation",
    "status",
)


class DatasetContractError(ValueError):
    """Raised when the dataset or the metric policy violates the Stage A contract."""


def _all_turns(data: dict | None = None) -> list[dict]:
    data = data if data is not None else DATA
    return [turn for session in data["sessions"] for turn in session["turns"]]


def validate_dataset(data: dict | None = None) -> dict:
    """Fail loudly on contract violations; return the account summary otherwise."""
    data = data if data is not None else DATA
    sessions = data.get("sessions", [])
    turns = _all_turns(data)
    problems: list[str] = []

    ids = [turn.get("turn_id") for turn in turns]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        problems.append(f"duplicate turn ids: {duplicates[:5]}")
    if len(sessions) != DATASET_CONTRACT["sessions"]:
        problems.append(f"expected {DATASET_CONTRACT['sessions']} sessions, found {len(sessions)}")
    if len(turns) != DATASET_CONTRACT["turns"]:
        problems.append(f"expected {DATASET_CONTRACT['turns']} turns, found {len(turns)}")

    behaviours = collections.Counter(turn.get("expected_behaviour") for turn in turns)
    for behaviour, expected in BEHAVIOUR_CONTRACT.items():
        if behaviours.get(behaviour, 0) != expected:
            found = behaviours.get(behaviour, 0)
            problems.append(f"expected {expected} {behaviour!r} turns, found {found}")

    rubrics = sum(1 for turn in turns if turn.get("geval_criteria"))
    if rubrics != RUBRIC_BEARING_TURNS:
        problems.append(f"expected {RUBRIC_BEARING_TURNS} rubric-bearing turns, found {rubrics}")

    for turn in turns:
        if turn.get("expected_behaviour") == "answer":
            for field in ("reference", "expected_sections", "standalone_input"):
                if not turn.get(field):
                    problems.append(f"{turn.get('turn_id')}: answer turn missing {field}")

    if problems:
        raise DatasetContractError("dataset contract violated: " + "; ".join(problems))

    return {
        "sessions": len(sessions),
        "turns": len(turns),
        "behaviours": dict(behaviours),
        "retrieval_applicable": behaviours["answer"],
        "pending_stage_b": behaviours["refuse"] + behaviours["clarify"],
        "rubric_bearing_turns": rubrics,
    }


def stage_a_applicability(turn: dict) -> dict[str, bool]:
    """Declare per-turn layer applicability exactly once.

    Retrieval: the 87 answerable standalone queries. Conversation: the 12
    refusal and one clarification turns, pending Stage B. Safety: every turn
    keeps its applicable checks. Generation: answer turns carry rubrics, but
    they only run when an actual application output exists (Step 9 decides).
    """
    behaviour = turn.get("expected_behaviour")
    return {
        "retrieval": behaviour == "answer",
        "safety": True,
        "generation": behaviour == "answer",
        "conversation": behaviour in CONVERSATION_BEHAVIOURS,
    }


def stage_a_applicability_summary(data: dict | None = None) -> dict[str, int]:
    totals = {"retrieval": 0, "safety": 0, "generation": 0, "conversation": 0}
    for turn in _all_turns(data):
        flags = stage_a_applicability(turn)
        for layer, applies in flags.items():
            if applies:
                totals[layer] += 1
    return totals


def retrieval_only_rows(data: dict | None = None) -> list[dict]:
    """One row per answerable turn for reference-based context metrics.

    No ``run`` callback, no generated answer: the caller evaluates the
    standalone question through real retrieval and attaches the actual
    contexts before the framework sees the row. ``must_not_retrieve`` and
    ``expected_sections`` ride along for the deterministic integrity checks.
    """
    rows: list[dict] = []
    for turn in _all_turns(data):
        if turn.get("expected_behaviour") != "answer":
            continue
        rows.append(
            {
                "turn_id": turn["turn_id"],
                "user_input": turn["standalone_input"],
                "reference": turn["reference"],
                "reference_contexts": turn.get("reference_contexts") or None,
                "expected_sections": turn["expected_sections"],
                "must_not_retrieve": turn.get("must_not_retrieve", []),
                "requires": turn.get("requires", []),
                "applicability": stage_a_applicability(turn),
            }
        )
    return rows


def load_metric_policy(path: str | Path | None = None) -> dict:
    """Load the Stage A metric policy; a missing or thin policy is never a pass."""
    policy_path = Path(path) if path else DEFAULT_POLICY_PATH
    if not policy_path.exists():
        raise FileNotFoundError(
            f"metric policy not found at {policy_path}: Stage A acceptance stays "
            "report-only, never a pass, until the policy exists and is signed"
        )
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    missing = [key for key in _POLICY_REQUIRED_KEYS if key not in policy]
    if missing:
        raise DatasetContractError(f"metric policy missing required keys: {missing}")
    if policy["status"] not in {"report_only", "agreed"}:
        raise DatasetContractError(
            f"metric policy status {policy['status']!r} is not one of report_only/agreed"
        )
    return policy


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-capabilities", action="store_true")
    ap.add_argument("--slice")
    a = ap.parse_args()
    if a.list_capabilities:
        c = collections.Counter(cap for t in turns() for cap in t["requires"])
        for cap, n in c.most_common():
            print(f"{n:3d}  {cap}")
    elif a.slice:
        for t in by_capability(a.slice):
            print(f"{t['turn_id']}  [{t['difficulty']}]  {t['input'][:80]}")
    else:
        d = DATA["dataset"]["counts"]
        print(f"{d['sessions']} sessions, {d['turns']} turns")
        print("behaviour:", d["by_behaviour"])
        print("difficulty:", d["by_difficulty"])
