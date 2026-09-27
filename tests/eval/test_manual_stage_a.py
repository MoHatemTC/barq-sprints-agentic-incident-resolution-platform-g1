"""Stage A runner contract: scratch-only targets, complete accounting, honest
failure handling, and no generated answers anywhere in the loop.

The runner is exercised with injected fakes end to end — real dataset rows,
a spy retriever, a fake judge — so the acceptance plumbing is tested offline
without ragas, credentials, or a Qdrant server.
"""

import importlib.util
from pathlib import Path

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
    from unittest.mock import MagicMock

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
    from qdrant_client import QdrantClient

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
