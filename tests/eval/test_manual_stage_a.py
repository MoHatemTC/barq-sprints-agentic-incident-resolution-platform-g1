"""Stage A runner contract: scratch-only targets, complete accounting, honest
failure handling, and no generated answers anywhere in the loop.

The runner is exercised with injected fakes end to end — real dataset rows,
a spy retriever, a fake judge — so the acceptance plumbing is tested offline
without ragas, credentials, or a Qdrant server.
"""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from qdrant_client import QdrantClient

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "eval" / "run_manual_stage_a.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("stage_a_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _spy_retriever(sections_for: dict[str, list[str]]):
    """Stands in for QdrantRetriever; records queries, maps article→sections."""

    class _Hit:
        def __init__(self, article_id: str, text: str) -> None:
            self.article_id = article_id
            self.text = text

    class _Result:
        def __init__(self, hits: list) -> None:
            self.hits = hits

    class _Spy:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def search(self, query: str, *, classification, top_k, threshold, **kwargs):
            self.queries.append(query)
            article_id, sections = next(iter(sections_for.items()))
            return _Result([_Hit(article_id, f"chunk of {article_id}")])

    return _Spy()


def _row(turn_id: str, must_not: list[str] | None = None) -> dict:
    return {
        "turn_id": turn_id,
        "user_input": f"question for {turn_id}",
        "reference": "reference answer text",
        "reference_contexts": ["gold passage"],
        "expected_sections": ["1.1"],
        "must_not_retrieve": must_not or [],
        "requires": ["factual"],
        "applicability": {
            "retrieval": True,
            "safety": True,
            "generation": True,
            "conversation": False,
        },
    }


def test_production_collection_and_missing_url_are_rejected() -> None:
    runner = _load_runner()
    parser = runner.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--collection", "scratch"])  # no --qdrant-url
    args = parser.parse_args(
        ["--qdrant-url", "http://localhost:16333", "--collection", "incident_knowledge_base"]
    )
    assert args.collection == "incident_knowledge_base"  # main() rejects it


def test_collect_contexts_uses_standalone_input_and_never_the_reference() -> None:
    runner = _load_runner()
    rows = [_row(f"S01-T{i}") for i in range(1, 4)]
    spy = _spy_retriever({"KB2021-v1.0": ["1.1"]})
    manifest = runner.load_manifest_for(REPO_ROOT / "data" / "corpus" / "manual_kb_manifest.json")

    records = runner.collect_contexts(
        rows, spy, runner.section_reverse_map(manifest), top_k=8, threshold=0.55
    )

    assert [r["turn_id"] for r in records] == [r["turn_id"] for r in rows]
    assert spy.queries == [r["user_input"] for r in rows], (
        "retrieval must see the standalone question, never the reference answer"
    )
    assert all(r["contexts"] and r["section_labels"] for r in records)


def test_forbidden_section_is_a_violation_not_a_score() -> None:
    runner = _load_runner()
    rows = [_row("S01-T1", must_not=["9.2"]), _row("S01-T2")]
    # KB2034 is §9.2 — the retired-instruction narrative.
    spy = _spy_retriever({"KB2034-v1.0": ["9.2"]})
    manifest = runner.load_manifest_for(REPO_ROOT / "data" / "corpus" / "manual_kb_manifest.json")
    records = runner.collect_contexts(
        rows, spy, runner.section_reverse_map(manifest), top_k=5, threshold=0.55
    )

    violations = runner.apply_integrity(rows, records)
    assert any("S01-T1" in v and "9.2" in v for v in violations)


def test_compound_forbidden_label_and_refusal_matching() -> None:
    runner = _load_runner()
    rows = [
        _row("S05-T1", must_not=["6.13 KB0010 v1"]),
        _row("S01-T5", must_not=["2.1", "2.3"]),
    ]

    # 1. Retired KB0010-v1.0 hit flags a violation
    records_bad = [
        {
            "turn_id": "S05-T1",
            "section_labels": [],
            "source_ids": ["KB0010-v1.0"],
            "contexts": ["retired text"],
        },
        {
            "turn_id": "S01-T5",
            "section_labels": ["2.1"],
            "source_ids": ["KB2002-v1.0"],
            "contexts": ["section 2.1 text"],
        },
    ]
    violations = runner.apply_integrity(rows, records_bad)
    assert any("S05-T1" in v and "6.13 KB0010 v1" in v for v in violations)
    assert any("S01-T5" in v and "2.1" in v for v in violations)

    # 2. Active KB0010-v2.0 with section 6.13 does NOT trigger a violation for v1
    records_good = [
        {
            "turn_id": "S05-T1",
            "section_labels": ["6.13"],
            "source_ids": ["KB0010-v2.0"],
            "contexts": ["active text"],
        },
        {
            "turn_id": "S01-T5",
            "section_labels": ["5.1"],
            "source_ids": ["KB2005-v1.0"],
            "contexts": ["unrelated text"],
        },
    ]
    violations_good = runner.apply_integrity(rows, records_good)
    assert len(violations_good) == 0


def test_missing_turn_is_reported() -> None:
    runner = _load_runner()
    rows = [_row("S01-T1"), _row("S01-T2")]
    records = [
        {
            "turn_id": "S01-T1",
            "contexts": ["c"],
            "section_labels": [],
            "source_ids": ["KB2021-v1.0"],
            "error": None,
        }
    ]
    violations = runner.apply_integrity(rows, records)
    assert any("missing" in v and "S01-T2" in v for v in violations)


def _mock_engine():
    from app.retrieval.embedding import EmbeddedText

    engine = MagicMock()
    engine.dense_vector_size = 384
    engine.embed_documents.side_effect = lambda docs: [
        EmbeddedText(dense=[0.1] * 384, sparse_indices=[1, 2], sparse_values=[0.5, 0.8])
        for _ in docs
    ]
    engine.embed_query.return_value = EmbeddedText(
        dense=[0.1] * 384, sparse_indices=[1, 2], sparse_values=[0.5, 0.8]
    )
    return engine


def test_run_with_fake_judge_is_pending_and_accounts_for_every_turn() -> None:
    runner = _load_runner()
    judge = runner.FakeJudge()

    report = runner.run(
        qdrant_url="http://localhost:16333",
        collection="scratch_eval",
        judge=judge,
        client_factory=lambda: QdrantClient(":memory:"),
        engine_factory=_mock_engine,
        limit=3,
        top_k=4,
        threshold=0.55,
    )

    assert report["acceptance"] == "pending", "a fake judge must never accept"
    assert report["judge"] == "fake"
    assert report["accounting"]["answerable_retrieval_rows"] == 87
    assert report["accounting"]["conversation_pending"] == 13
    assert len(report["rows"]) == 3
    assert all(
        r["scores"] == {"context_recall": 0.5, "context_precision": 0.5} for r in report["rows"]
    )


def test_judge_failure_is_recorded_not_passed() -> None:
    runner = _load_runner()

    class _FailingJudge(runner.FakeJudge):
        def score(self, row, contexts):
            if row["turn_id"].endswith("T2"):
                raise RuntimeError("judge exploded")
            return {"context_recall": 1.0, "context_precision": 1.0}

    report = runner.run(
        qdrant_url="http://localhost:16333",
        collection="scratch_eval",
        judge=_FailingJudge(),
        client_factory=lambda: QdrantClient(":memory:"),
        engine_factory=_mock_engine,
        limit=2,
    )
    by_id = {r["turn_id"]: r for r in report["rows"]}
    assert by_id["S01-T1"]["scores"] is not None
    assert by_id["S01-T2"]["scores"] is None
    assert "judge exploded" in by_id["S01-T2"]["judge_error"]
    assert report["accounting"]["judge_errors"] == 1
    assert report["acceptance"] == "failed"


def test_retrieval_failure_fails_acceptance() -> None:
    runner = _load_runner()
    failing_engine = _mock_engine()
    failing_engine.embed_query.side_effect = RuntimeError("embedding service down")

    report = runner.run(
        qdrant_url="http://localhost:16333",
        collection="scratch_eval",
        judge=runner.FakeJudge(),
        client_factory=lambda: QdrantClient(":memory:"),
        engine_factory=lambda: failing_engine,
        limit=2,
    )
    assert report["acceptance"] == "failed"
    assert report["accounting"]["retrieval_errors"] == 2


def test_metric_floor_failure_marks_run_failed(monkeypatch) -> None:
    runner = _load_runner()
    # Inject a policy with a floor of 0.80, while FakeJudge returns 0.50
    strict_policy = {
        "description": "Strict policy with signed floor",
        "framework": "ragas",
        "framework_version": "1.0",
        "judge_model": "test-judge",
        "metrics": {
            "context_recall": {"floor": 0.80, "aggregation": "per capability slice"},
            "context_precision": {"floor": 0.80, "aggregation": "per capability slice"},
        },
        "status": "agreed",
        "thresholds_agreed": True,
        "safety": {"zero_tolerance": []},
    }
    monkeypatch.setattr(runner.adapters, "load_metric_policy", lambda: strict_policy)

    report = runner.run(
        qdrant_url="http://localhost:16333",
        collection="scratch_eval",
        judge=runner.FakeJudge(),
        client_factory=lambda: QdrantClient(":memory:"),
        engine_factory=_mock_engine,
        limit=2,
    )
    assert report["acceptance"] == "failed"
    assert len(report["floor_failures"]) > 0
    assert any("context_recall" in f for f in report["floor_failures"])


def test_nan_or_none_metric_scores_fail_acceptance(monkeypatch) -> None:
    runner = _load_runner()
    agreed_policy = {
        "description": "Agreed policy",
        "framework": "ragas",
        "framework_version": "1.0",
        "judge_model": "test-judge",
        "metrics": {
            "context_recall": {"floor": 0.50, "aggregation": "per capability slice"},
            "context_precision": {"floor": 0.50, "aggregation": "per capability slice"},
        },
        "status": "agreed",
        "thresholds_agreed": True,
        "safety": {"zero_tolerance": []},
    }
    monkeypatch.setattr(runner.adapters, "load_metric_policy", lambda: agreed_policy)

    class _NanJudge:
        name = "ragas"

        def score(self, row, contexts):
            return {"context_recall": float("nan"), "context_precision": None}

    report = runner.run(
        qdrant_url="http://localhost:16333",
        collection="scratch_eval",
        judge=_NanJudge(),
        client_factory=lambda: QdrantClient(":memory:"),
        engine_factory=_mock_engine,
        limit=2,
    )
    assert report["accounting"]["evaluated"] == 0
    assert report["acceptance"] == "failed"


def test_partial_smoke_run_is_incomplete_not_agreed(monkeypatch) -> None:
    runner = _load_runner()
    agreed_policy = {
        "description": "Agreed policy",
        "framework": "ragas",
        "framework_version": "1.0",
        "judge_model": "test-judge",
        "metrics": {
            "context_recall": {"floor": 0.50, "aggregation": "per capability slice"},
            "context_precision": {"floor": 0.50, "aggregation": "per capability slice"},
        },
        "status": "agreed",
        "thresholds_agreed": True,
        "safety": {"zero_tolerance": []},
    }
    monkeypatch.setattr(runner.adapters, "load_metric_policy", lambda: agreed_policy)

    class _RagasJudge:
        name = "ragas"

        def score(self, row, contexts):
            return {"context_recall": 1.0, "context_precision": 1.0}

    report = runner.run(
        qdrant_url="http://localhost:16333",
        collection="scratch_eval",
        judge=_RagasJudge(),
        client_factory=lambda: QdrantClient(":memory:"),
        engine_factory=_mock_engine,
        limit=1,
    )
    assert report["accounting"]["evaluated"] == 1
    assert report["acceptance"] == "incomplete", "partial smoke run must be incomplete, not agreed"


def test_negative_safety_retrieval_error_fails_acceptance() -> None:
    runner = _load_runner()
    failing_engine = _mock_engine()
    orig_embed = failing_engine.embed_query

    # Fail embedding only when querying safety negative queries
    safety_negative_turns = [
        t
        for t in runner.adapters.turns()
        if t.get("expected_behaviour") in ("refuse", "clarify") and t.get("must_not_retrieve")
    ]
    target_inputs = {t.get("standalone_input") or t.get("input", "") for t in safety_negative_turns}

    def _conditional_embed(text: str) -> list[float]:
        if text in target_inputs:
            raise RuntimeError("embedding backend down on safety negative query")
        return orig_embed(text)

    failing_engine.embed_query.side_effect = _conditional_embed

    report = runner.run(
        qdrant_url="http://localhost:16333",
        collection="scratch_eval",
        judge=runner.FakeJudge(),
        client_factory=lambda: QdrantClient(":memory:"),
        engine_factory=lambda: failing_engine,
        limit=None,
    )
    assert report["accounting"]["retrieval_errors"] > 0
    assert report["accounting"]["safety_negative_rows"] > 0
    assert report["acceptance"] == "failed"
    # Ensure safety records are preserved in report["rows"]
    row_ids = {r["turn_id"] for r in report["rows"]}
    for t in safety_negative_turns:
        assert t["turn_id"] in row_ids
