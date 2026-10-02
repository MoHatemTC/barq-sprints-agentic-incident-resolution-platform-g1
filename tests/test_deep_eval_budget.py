"""No-network regressions for paid-call counts, checkpoint reuse and budget stops."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import eval.deep_eval as evaluator
from eval.deep_eval import evaluate_dataset, judge_answer, parse_args
from eval.eval_runtime import BudgetedLLM, BudgetExceeded, Checkpoint
from eval.generate_stage_b_report import main as report_main


@pytest.mark.parametrize("ranked,expected,value", [
    (["noise", "KB1", "KB1"], ["KB1"], 0.5),
    (["KB1", "KB2"], ["KB2", "KB1"], 1.0),
    (["noise"], ["KB1"], 0.0),
    ([], ["KB1"], 0.0),
    (["noise"], ["—"], None),
])
def test_reciprocal_rank_preserves_retrieval_order(ranked, expected, value):
    from data.corpus.adapters import score_retrieval

    result = score_retrieval(iter(ranked), {
        "turn_id": "test", "expected_sections": expected, "must_not_retrieve": [],
    })
    assert result["reciprocal_rank"] == value


def test_appendix_parent_matches_any_child_without_requiring_every_template():
    from data.corpus.adapters import score_retrieval

    result = score_retrieval(["noise", "KB1402"], {
        "turn_id": "appendices", "expected_sections": ["Appendix B"],
        "expected_label_groups": {"Appendix B": ["KB1401", "KB1402", "KB1403", "KB1404"]},
        "must_not_retrieve": [],
    })
    assert result["recall"] == 1.0
    assert result["precision"] == 0.5
    assert result["reciprocal_rank"] == 0.5
    assert result["top_k_contains_all"] is True


def test_mrr_average_excludes_refusal_turns(tmp_path):
    data = fixture_data(("answer", "refuse"))
    llm, _ = make_llm(tmp_path, auto_client)
    output = tmp_path / "results.json"
    retrieval = Mock(return_value=[
        {"section": "noise", "title": "Noise", "chunk_text": "Other", "score": 0.9},
        {"section": "1.1", "title": "Purpose", "chunk_text": "Evidence", "score": 0.8},
    ])
    evaluate_dataset(data, retrieval, llm, args_for_test(), output)
    artifact = json.loads(output.read_text())
    assert artifact["summary"]["retrieval"]["mrr"] == 0.5
    assert artifact["turns"][1]["retrieval"]["reciprocal_rank"] is None


def test_long_judge_explanation_is_preserved_without_rejudging(tmp_path):
    data = fixture_data()
    reason = "Detailed evidence explanation. " * 20

    def respond(**options):
        raw = auto_client(**options)
        if "response_format" in options:
            parsed = json.loads(raw.parse().choices[0].message.content)
            for value in parsed["metrics"].values():
                value["reason"] = reason
            return raw_response(json.dumps(parsed))
        return raw

    llm, _ = make_llm(tmp_path, respond)
    turn = data["sessions"][0]["turns"][0]
    judge_answer(turn, "answer", retrieve()(), data, llm, args_for_test(), "key")
    resumed, create = make_llm(tmp_path)
    row = judge_answer(turn, "answer", retrieve()(), data, resumed, args_for_test(), "key")
    create.assert_not_called()
    assert row["metrics"]["grounding"]["reason"] == reason


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
    assert args.judge_model == "google/gemini-3.5-flash-lite"


def test_smoke_refusal_example_and_fabrication_boundary_reach_the_judge(tmp_path):
    answer = (
        "Based on the provided sections of the manual, there is no mention of a policy "
        "regarding remote working for analysts."
    )
    data = fixture_data(("refuse",))
    turn = data["sessions"][0]["turns"][0]
    turn.update(
        standalone_input="What's the desk's policy on remote working for analysts?",
        reference="The manual does not cover remote working for analysts.",
        geval_criteria=(
            "Must not invent a policy or present coverage hours as remote-working policy."
        ),
    )

    def check_request(**options):
        system = options["messages"][0]["content"]
        payload = json.loads(options["messages"][1]["content"])
        assert answer in system.replace("\n", " ")
        assert payload["candidate_answer"] == answer
        assert 'An explicit "I cannot answer" is unnecessary.' in system
        assert "three days a week" in system
        assert "must fail refusal quality" in payload["rubrics"]["refusal_quality"]
        assert payload["rubrics"]["turn_rubric"] == turn["geval_criteria"]
        return raw_response(
            json.dumps(
                {
                    "actual_behaviour": "refuse",
                    "metrics": {
                        name: {"score": 1.0, "reason": "No policy invented"}
                        for name in payload["rubrics"]
                    },
                }
            )
        )

    llm, create = make_llm(tmp_path, check_request)
    row = judge_answer(turn, answer, retrieve()(), data, llm, args_for_test(), "cached-answer")
    assert row["verdict"] == "PASS"
    assert row["actual_behaviour"] == "refuse"
    assert create.call_count == 1


def test_judge_prompt_revision_reuses_answer_but_invalidates_old_judgment(tmp_path, monkeypatch):
    data = fixture_data(("refuse",))
    output = tmp_path / "results.json"
    current_prompt = evaluator.JUDGE_SYSTEM_PROMPT
    monkeypatch.setattr(evaluator, "JUDGE_SYSTEM_PROMPT", "previous judge prompt")
    llm, _ = make_llm(tmp_path, auto_client)
    assert evaluate_dataset(data, retrieve(), llm, args_for_test(), output) == 0
    monkeypatch.setattr(evaluator, "JUDGE_SYSTEM_PROMPT", current_prompt)
    resumed, create = make_llm(tmp_path, auto_client)
    retrieval = retrieve()
    assert evaluate_dataset(data, retrieval, resumed, args_for_test(), output) == 0
    retrieval.assert_not_called()
    assert create.call_count == 1
    assert create.call_args.kwargs["response_format"] == {"type": "json_object"}


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


def test_dataset_recommended_metrics_and_reference_contexts_reach_one_judge(tmp_path):
    dataset = json.loads(evaluator.DATASET.read_text())
    turn = dataset["sessions"][0]["turns"][0]
    args = args_for_test()

    def check_request(**options):
        payload = json.loads(options["messages"][1]["content"])
        assert {
            "faithfulness",
            "answer_relevancy",
            "contextual_precision",
            "contextual_recall",
            "grounding",
            "citation",
            "safety",
            "over_refusal",
            "completeness",
        } == set(payload["rubrics"])
        assert payload["reference_contexts"] == turn["reference_contexts"]
        assert payload["section_to_article"] == {"1.1": "KB0101"}
        return auto_client(**options)

    llm, create = make_llm(tmp_path, check_request)
    assert (
        judge_answer(
            turn, "answer", retrieve()(), dataset, llm, args, "answer-cache", {"1.1": "KB0101"}
        )["verdict"]
        == "PASS"
    )
    assert create.call_count == 1


def test_refusal_suite_includes_hallucination_without_answer_only_metrics():
    dataset = json.loads(evaluator.DATASET.read_text())
    turn = dataset["sessions"][0]["turns"][4]
    rubrics = evaluator.rubrics_for_turn(turn, dataset)
    assert {"hallucination_free", "refusal_quality", "safety", "turn_rubric"} == set(rubrics)
    assert "higher-is-better" in rubrics["hallucination_free"]


def test_metric_threshold_change_uses_cached_judgment(tmp_path):
    output = tmp_path / "results.json"
    llm, _ = make_llm(tmp_path, auto_client)
    evaluate_dataset(fixture_data(), retrieve(), llm, args_for_test(), output)
    resumed, create = make_llm(tmp_path)
    args = args_for_test(metric_thresholds={"faithfulness": 0.9})
    assert evaluate_dataset(fixture_data(), retrieve(), resumed, args, output) == 1
    create.assert_not_called()
    row = json.loads(output.read_text())["turns"][0]
    assert row["metrics"]["faithfulness"]["threshold"] == 0.9
    assert not row["metrics"]["faithfulness"]["success"]
    assert row["metrics"]["answer_relevancy"]["success"]


def test_kb_labels_align_to_manual_sections_before_adapter_scoring(tmp_path):
    data = fixture_data()
    turn = data["sessions"][0]["turns"][0]
    turn["must_not_retrieve"] = ["9.9"]
    retrieval = Mock(
        return_value=[
            {"section": "KB0101", "title": "Purpose", "chunk_text": "Evidence", "score": 0.9},
            {"section": "KB0909", "title": "Forbidden", "chunk_text": "Evidence", "score": 0.8},
        ]
    )
    llm, _ = make_llm(tmp_path, auto_client)
    output = tmp_path / "results.json"
    evaluate_dataset(
        data,
        retrieval,
        llm,
        args_for_test(),
        output,
        section_map={"1.1": "KB0101", "9.9": "KB0909"},
    )
    row = json.loads(output.read_text())["turns"][0]
    assert row["retrieval"]["recall"] == 1.0
    assert row["retrieval"]["precision"] == 0.5
    assert row["retrieval"]["forbidden_retrieved"] == ["KB0909"]
    assert not row["retrieval"]["clean"]
    assert row["retrieval"]["expected_labels"] == ["KB0101"]
    assert "retrieval_clean" not in row["metrics"]
    assert row["verdict"] == "PASS"


def test_appendix_alias_and_inspection_report(tmp_path):
    data = fixture_data()
    turn = data["sessions"][0]["turns"][0]
    turn["expected_sections"] = ["Appendix C"]
    turn["reference_contexts"] = ["Expected worksheet passage"]
    retrieval = Mock(return_value=[{
        "section": "KB1500", "title": "Worksheet", "chunk_text": "Retrieved worksheet passage",
        "score": 0.9,
    }])
    llm, create = make_llm(tmp_path, auto_client)
    output = tmp_path / "results.json"
    evaluate_dataset(data, retrieval, llm, args_for_test(), output, section_map={"C": "KB1500"})
    row = json.loads(output.read_text())["turns"][0]
    assert row["retrieval"]["recall"] == 1.0
    assert create.call_count == 2
    report = tmp_path / "report.md"
    assert report_main(["--results", str(output), "--output", str(report)]) == 0
    text = report.read_text()
    assert "Retrieved worksheet passage" in text
    assert "KB1500 (§C)" in text
    assert "Expected worksheet passage" in text
    assert "**Expected answer:**" in text
    assert "Judge explanation" in text
    assert row["retrieval"]["expected_labels"] == ["KB1500"]
    assert "retrieval_clean" not in row["metrics"]
    assert row["verdict"] == "PASS"


def test_explicit_token_retry_preserves_earlier_turn_cache(tmp_path):
    data = fixture_data(("answer", "answer"))
    output = tmp_path / "results.json"
    llm, _ = make_llm(tmp_path, auto_client)
    evaluate_dataset(data, retrieve(), llm, args_for_test(), output)
    resumed, create = make_llm(tmp_path, auto_client)
    args = args_for_test()
    args.gen_retry_turn = "S01-T2"
    retrieval = retrieve()
    evaluate_dataset(data, retrieval, resumed, args, output)
    assert create.call_count == 2
    assert retrieval.call_count == 1
    assert create.call_args_list[0].kwargs["max_completion_tokens"] == args.gen_max_tokens * 2


def test_missing_kb_mapping_stops_before_judging(tmp_path):
    llm, create = make_llm(tmp_path, auto_client)
    retrieval = Mock(
        return_value=[
            {"section": "KB0101", "title": "Purpose", "chunk_text": "Evidence", "score": 0.9},
        ]
    )
    output = tmp_path / "results.json"
    assert (
        evaluate_dataset(fixture_data(), retrieval, llm, args_for_test(), output, section_map={})
        == 2
    )
    assert create.call_count == 1
    assert "Missing section-to-article" in json.loads(output.read_text())["turns"][0]["error"]


@pytest.mark.parametrize("override", ["faithfulness=nan", "unknown=0.8", "safety=1.1", "safety"])
def test_bad_metric_threshold_is_rejected(override):
    with pytest.raises(SystemExit):
        parse_args(["--metric-threshold", override])


def test_valid_independent_metric_thresholds():
    args = parse_args(["--metric-threshold", "faithfulness=0.9", "--metric-threshold", "safety=1"])
    assert args.metric_thresholds == {"faithfulness": 0.9, "safety": 1.0}
    assert args.threshold == 0.7


def test_correct_refusal_passes_despite_retrieved_distractor(tmp_path):
    data = fixture_data(('refuse',))
    turn = data['sessions'][0]['turns'][0]
    turn['expected_sections'] = ['—']
    turn['must_not_retrieve'] = ['2.1']
    retrieval = Mock(return_value=[
        {'section': 'KB0201', 'title': 'Coverage hours', 'chunk_text': 'Coverage hours', 'score': 0.9},
    ])
    llm, create = make_llm(tmp_path, auto_client)
    output = tmp_path / 'results.json'
    assert evaluate_dataset(data, retrieval, llm, args_for_test(), output,
                            section_map={'2.1': 'KB0201'}) == 0
    row = json.loads(output.read_text())['turns'][0]
    assert row['actual_behaviour'] == 'refuse'
    assert row['retrieval']['forbidden_retrieved'] == ['KB0201']
    assert 'retrieval_clean' not in row['metrics']
    assert row['verdict'] == 'PASS'
    assert create.call_count == 2
