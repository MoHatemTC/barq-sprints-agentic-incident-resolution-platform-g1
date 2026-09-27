"""Stage A dataset contract: validation, applicability, retrieval-only loading.

The 100-turn dataset is the sole Stage A gate (the legacy suite is retired).
These tests pin its contract so a mutated or truncated dataset fails loudly,
declare per-turn layer applicability, and prove the retrieval-only loader
never generates answers — RAGAS grades retrieved contexts against references,
not chat output. A missing or unset metric policy is report-only, never a pass.
"""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ADAPTERS = Path(__file__).resolve().parents[2] / "data" / "structured-io" / "adapters.py"
POLICY_PATH = Path(__file__).resolve().parents[2] / "eval" / "manual_stage_a_policy.json"


def _load_adapters():
    spec = importlib.util.spec_from_file_location("barq_eval_adapters_contract", ADAPTERS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mutated_dataset() -> dict:
    module = _load_adapters()
    return copy.deepcopy(module.DATA)


class TestDatasetValidation:
    def test_real_dataset_passes_the_contract(self) -> None:
        module = _load_adapters()
        summary = module.validate_dataset()
        assert summary["sessions"] == 20
        assert summary["turns"] == 100
        assert summary["retrieval_applicable"] == 87
        assert summary["pending_stage_b"] == 13
        assert summary["rubric_bearing_turns"] == 35

    def test_duplicate_turn_ids_are_rejected(self) -> None:
        module = _load_adapters()
        data = _mutated_dataset()
        data["sessions"][0]["turns"][1]["turn_id"] = data["sessions"][0]["turns"][0]["turn_id"]
        with pytest.raises(module.DatasetContractError, match="duplicate turn ids"):
            module.validate_dataset(data)

    def test_wrong_turn_count_is_rejected(self) -> None:
        module = _load_adapters()
        data = _mutated_dataset()
        del data["sessions"][0]["turns"][-1]
        with pytest.raises(module.DatasetContractError, match="100"):
            module.validate_dataset(data)

    def test_answer_turn_missing_reference_is_rejected(self) -> None:
        module = _load_adapters()
        data = _mutated_dataset()
        answer = next(
            t for s in data["sessions"] for t in s["turns"] if t["expected_behaviour"] == "answer"
        )
        answer["reference"] = ""
        with pytest.raises(module.DatasetContractError, match=answer["turn_id"]):
            module.validate_dataset(data)

    def test_lost_custom_rubrics_are_rejected(self) -> None:
        module = _load_adapters()
        data = _mutated_dataset()
        stripped = 0
        for session in data["sessions"]:
            for turn in session["turns"]:
                if turn.get("geval_criteria"):
                    del turn["geval_criteria"]
                    stripped += 1
        assert stripped == 35
        with pytest.raises(module.DatasetContractError, match="rubric"):
            module.validate_dataset(data)


class TestApplicability:
    def test_layers_are_declared_for_every_turn(self) -> None:
        module = _load_adapters()
        totals = module.stage_a_applicability_summary()
        assert totals["retrieval"] == 87
        assert totals["conversation"] == 13
        assert totals["safety"] == 100
        assert totals["generation"] == 87

    def test_negative_turns_are_pending_stage_b_not_retrieval(self) -> None:
        module = _load_adapters()
        negative = next(
            t
            for s in module.DATA["sessions"]
            for t in s["turns"]
            if t["expected_behaviour"] in ("refuse", "clarify")
        )
        flags = module.stage_a_applicability(negative)
        assert flags["retrieval"] is False
        assert flags["conversation"] is True
        assert flags["safety"] is True


class TestRetrievalOnlyLoader:
    def test_rows_cover_exactly_the_87_answerable_turns(self) -> None:
        module = _load_adapters()
        rows = module.retrieval_only_rows()
        assert len(rows) == 87
        assert all(row["user_input"] for row in rows)
        assert all(row["reference"] for row in rows)
        assert all(row["expected_sections"] for row in rows)

    def test_rows_map_standalone_query_and_carry_no_answer(self) -> None:
        module = _load_adapters()
        by_id = {t["turn_id"]: t for s in module.DATA["sessions"] for t in s["turns"]}
        row = next(r for r in module.retrieval_only_rows() if r["turn_id"] == "S17-T5")
        assert row["user_input"] == by_id["S17-T5"]["standalone_input"]
        assert "response" not in row and "actual_output" not in row
        assert row["applicability"]["retrieval"] is True

class TestMetricPolicy:
    def test_missing_policy_file_is_an_error_not_a_pass(self, tmp_path: Path) -> None:
        module = _load_adapters()
        with pytest.raises(FileNotFoundError, match="report-only"):
            module.load_metric_policy(tmp_path / "missing.json")

    def test_committed_policy_is_report_only_with_unset_floors(self) -> None:
        module = _load_adapters()
        policy = module.load_metric_policy()
        assert policy["framework"] == "ragas"
        assert policy["status"] == "report_only"
        assert policy["thresholds_agreed"] is False
        for metric in ("context_recall", "context_precision"):
            assert metric in policy["metrics"]
            assert policy["metrics"][metric]["floor"] is None

    def test_policy_missing_required_keys_is_rejected(self, tmp_path: Path) -> None:
        module = _load_adapters()
        path = tmp_path / "policy.json"
        path.write_text(json.dumps({"framework": "ragas"}))
        with pytest.raises(module.DatasetContractError, match="required keys"):
            module.load_metric_policy(path)
