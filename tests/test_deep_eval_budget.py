"""No-network regressions for paid-call counts, checkpoint reuse and budget stops."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from eval.deep_eval import evaluate_dataset, parse_args
from eval.eval_runtime import BudgetedLLM, BudgetExceeded, Checkpoint
from eval.generate_stage_b_report import main as report_main


def raw_response(content, *, cost=None, usage=True, finish_reason="stop"):
    return SimpleNamespace(
        headers={} if cost is None else {"x-litellm-response-cost": str(cost)},
        parse=lambda: SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50) if usage else None,
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content), finish_reason=finish_reason
                )
            ],
        ),
    )


def make_llm(tmp_path, responses=None, budget=0.8, max_calls=220):
    create = Mock(side_effect=responses)
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=create))
        )
    )
    llm = BudgetedLLM(client, Checkpoint(tmp_path / "checkpoint.json"), budget, max_calls)
    return llm, create


def complete(llm, **overrides):
    options = dict(
        stage="judge",
        turn_id="S01-T1",
        model="gemini/test",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=100,
        input_rate=1.0,
        output_rate=1.0,
    )
    return llm.complete(**(options | overrides))


def fixture_data(behaviours=("answer",)):
    return {
        "metric_suite": {
            "geval_global_rubrics": {
                "grounding": "Use the evidence",
                "citation": "Cite the supporting section",
                "over_refusal": "Do not refuse answerable questions",
                "safety": "No secrets",
                "refusal_quality": "Refuse unsupported questions",
            }
        },
        "sessions": [
            {
                "session_id": "S01",
                "turns": [
                    {
                        "turn_id": f"S01-T{i}",
                        "input": f"question {i}",
                        "standalone_input": f"question {i}",
                        "reference": "reference",
                        "expected_behaviour": behaviour,
                        "expected_sections": ["1.1"],
                        "must_not_retrieve": ["9.9"],
                        "requires": ["single_hop"],
                        "difficulty": "easy",
                        "geval_criteria": None,
                    }
                    for i, behaviour in enumerate(behaviours, 1)
                ],
            }
        ],
    }


def auto_client(**options):
    if options.get("response_format"):
        payload = json.loads(options["messages"][-1]["content"])
        return raw_response(
            json.dumps(
                {
                    "actual_behaviour": payload["expected_behaviour"],
                    "metrics": {
                        name: {"score": 0.8, "reason": "supported"} for name in payload["rubrics"]
                    },
                }
            )
        )
    return raw_response("answer (§1.1)")


def args_for_test(**changes):
    args = parse_args([])
    args.gen_model = "gemini/test"
    args.url = "http://unused"
    for name, value in changes.items():
        setattr(args, name, value)
    return args


def retrieve():
    return Mock(
        return_value=[
            {"section": "1.1", "title": "title", "chunk_text": "reference evidence", "score": 0.9}
        ]
    )


def test_missing_header_counts_calls_and_estimates_tokens(tmp_path):
    llm, create = make_llm(tmp_path, [raw_response("answer")])
    complete(llm)
    usage = llm.usage_summary()
    assert create.call_count == 1
    assert usage["llm_calls_this_run"] == 1
    assert usage["spend_this_run_usd"] is None
    assert usage["accounted_spend_usd"] == pytest.approx(0.00015)
    assert usage["prompt_tokens"] == 100
    assert llm.checkpoint.data["calls"][0]["cost_source"] == "token_estimate"


def test_header_overrides_estimate_and_survives_restart(tmp_path):
    llm, _ = make_llm(tmp_path, [raw_response("answer", cost=0.03)])
    complete(llm)
    restarted, create = make_llm(tmp_path)
    assert complete(restarted)["content"] == "answer"
    create.assert_not_called()
    assert restarted.usage_summary()["observed_proxy_spend_usd"] == 0.03
    assert restarted.usage_summary()["llm_calls_this_run"] == 0


def test_reservation_blocks_call_before_dispatch(tmp_path):
    llm, create = make_llm(tmp_path, budget=0.000001)
    with pytest.raises(BudgetExceeded):
        complete(llm)
    create.assert_not_called()
    assert llm.checkpoint.data["calls"] == []


def test_no_header_or_usage_keeps_reservation(tmp_path):
    llm, _ = make_llm(tmp_path, [raw_response("answer", usage=False)])
    complete(llm)
    call = llm.checkpoint.data["calls"][0]
    assert call["cost_source"] == "reservation"
    assert call["accounted_usd"] > 0
    assert llm.usage_summary()["observed_proxy_spend_usd"] is None


def test_ambiguous_failure_is_not_retried_and_keeps_reservation(tmp_path):
    llm, create = make_llm(tmp_path, [TimeoutError("mock timeout")])
    with pytest.raises(TimeoutError):
        complete(llm)
    assert create.call_count == 1
    call = llm.checkpoint.data["calls"][0]
    assert call["status"] == "error"
    assert call["accounted_usd"] > 0


def test_call_limit_persists_across_restarts(tmp_path):
    llm, _ = make_llm(tmp_path, [raw_response("answer")], max_calls=1)
    complete(llm)
    restarted, create = make_llm(tmp_path, max_calls=1)
    with pytest.raises(BudgetExceeded):
        complete(restarted, messages=[{"role": "user", "content": "different"}])
    create.assert_not_called()


def test_two_calls_per_turn_and_rerun_reuses_everything(tmp_path):
    data = fixture_data(("answer", "refuse", "clarify"))
    args = args_for_test()
    output = tmp_path / "results.json"
    llm, create = make_llm(tmp_path, auto_client)
    retrieval = retrieve()
    assert evaluate_dataset(data, retrieval, llm, args, output) == 0
    assert create.call_count == 6
    assert retrieval.call_count == 3
    report = json.loads(output.read_text())
    assert report["status"] == "complete"
    assert report["turns"][2]["actual_behaviour"] == "clarify"
    restarted, second_create = make_llm(tmp_path)
    second_retrieve = retrieve()
    assert evaluate_dataset(data, second_retrieve, restarted, args, output) == 0
    second_create.assert_not_called()
    second_retrieve.assert_not_called()
    assert json.loads(output.read_text())["usage"]["llm_calls_this_run"] == 0


def test_threshold_change_rescores_without_api_calls(tmp_path):
    output = tmp_path / "results.json"
    llm, _ = make_llm(tmp_path, auto_client)
    assert evaluate_dataset(fixture_data(), retrieve(), llm, args_for_test(), output) == 0
    restarted, create = make_llm(tmp_path)
    assert (
        evaluate_dataset(
            fixture_data(), retrieve(), restarted, args_for_test(threshold=0.9), output
        )
        == 1
    )
    create.assert_not_called()
    assert json.loads(output.read_text())["turns"][0]["verdict"] == "FAIL"


def test_bad_judgment_preserves_answer_and_requires_explicit_retry(tmp_path):
    output = tmp_path / "results.json"
    llm, create = make_llm(tmp_path, [raw_response("answer (§1.1)"), raw_response("bad JSON")])
    assert evaluate_dataset(fixture_data(), retrieve(), llm, args_for_test(), output) == 2
    assert create.call_count == 2
    assert len(llm.checkpoint.data["generations"]) == 1
    restarted, repeat_create = make_llm(tmp_path)
    assert evaluate_dataset(fixture_data(), retrieve(), restarted, args_for_test(), output) == 2
    repeat_create.assert_not_called()
    retry_llm, retry_create = make_llm(tmp_path, auto_client)
    retrieval = retrieve()
    assert (
        evaluate_dataset(
            fixture_data(), retrieval, retry_llm, args_for_test(judge_revision="2"), output
        )
        == 0
    )
    assert retry_create.call_count == 1
    retrieval.assert_not_called()


def test_budget_stop_after_generation_resumes_judging_only(tmp_path):
    output = tmp_path / "results.json"
    llm, create = make_llm(tmp_path, auto_client, max_calls=1)
    assert evaluate_dataset(fixture_data(), retrieve(), llm, args_for_test(), output) == 2
    assert create.call_count == 1
    assert len(llm.checkpoint.data["generations"]) == 1
    resumed, resumed_create = make_llm(tmp_path, auto_client)
    retrieval = retrieve()
    assert evaluate_dataset(fixture_data(), retrieval, resumed, args_for_test(), output) == 0
    assert resumed_create.call_count == 1
    retrieval.assert_not_called()


def test_judge_model_change_reuses_generation(tmp_path):
    output = tmp_path / "results.json"
    llm, _ = make_llm(tmp_path, auto_client)
    evaluate_dataset(fixture_data(), retrieve(), llm, args_for_test(), output)
    restarted, create = make_llm(tmp_path, auto_client)
    retrieval = retrieve()
    evaluate_dataset(
        fixture_data(), retrieval, restarted, args_for_test(judge_model="gemini/other"), output
    )
    assert create.call_count == 1
    retrieval.assert_not_called()


def test_corpus_version_invalidates_generation(tmp_path):
    output = tmp_path / "results.json"
    llm, _ = make_llm(tmp_path, auto_client)
    evaluate_dataset(fixture_data(), retrieve(), llm, args_for_test(), output)
    restarted, create = make_llm(tmp_path, auto_client)
    retrieval = retrieve()
    evaluate_dataset(
        fixture_data(), retrieval, restarted, args_for_test(corpus_version="new"), output
    )
    assert create.call_count == 2
    assert retrieval.call_count == 1


def test_invalid_json_does_not_become_a_scored_failure(tmp_path):
    output = tmp_path / "results.json"
    llm, _ = make_llm(tmp_path, [raw_response("answer"), raw_response("{}")])
    evaluate_dataset(fixture_data(), retrieve(), llm, args_for_test(), output)
    report = json.loads(output.read_text())
    assert report["summary"]["verdicts"] == {"ERROR": 1}
    assert report["summary"]["scored_turns"] == 0
    assert report["summary"]["per_metric"] == {}
    markdown = tmp_path / "report.md"
    report_main(["--results", str(output), "--output", str(markdown)])
    text = markdown.read_text()
    assert "Incomplete run" in text
    assert "Observed proxy spend: unknown" in text
    assert "not the built-in DeepEval" in text


def test_incomplete_output_is_cached_and_not_judged(tmp_path):
    llm, create = make_llm(tmp_path, [raw_response("partial", finish_reason="length")])
    assert (
        evaluate_dataset(
            fixture_data(), retrieve(), llm, args_for_test(), tmp_path / "results.json"
        )
        == 2
    )
    assert create.call_count == 1


@pytest.mark.parametrize(
    "option,value",
    [
        ("--limit", "-1"),
        ("--threshold", "nan"),
        ("--threshold", "1.1"),
        ("--budget-usd", "nan"),
        ("--budget-usd", "0"),
        ("--gen-model", "openai/test"),
    ],
)
def test_invalid_configuration_rejected_without_calls(option, value):
    with pytest.raises(SystemExit):
        parse_args([option, value])


def test_openrouter_uses_lighter_google_model_by_default():
    args = parse_args(["--base-url", "https://openrouter.ai/api/v1"])
    assert args.judge_model == "google/gemini-2.5-flash-lite"


@pytest.mark.parametrize("header", [None, 0.004])
def test_openrouter_cost_is_counted_once(tmp_path, header):
    raw = raw_response("answer", cost=header)
    response = raw.parse()
    response.usage.model_extra = {"cost": 0.004}
    raw.parse = lambda: response
    llm, _ = make_llm(tmp_path, [raw])
    complete(llm)
    assert llm.usage_summary()["observed_proxy_spend_usd"] == 0.004
    assert llm.usage_summary()["llm_calls_total"] == 1
