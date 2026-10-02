"""Render eval/stage_b_report.md from the DeepEval run JSON.

Follows the repo rule: every number in the report traces to the run artifact
(eval/deepeval_results.json) — nothing is recomputed or hand-edited here
beyond averaging already-stored scores.

Run with: .venv/bin/python eval/generate_stage_b_report.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "eval" / "deepeval_results.json"
REPORT = REPO / "eval" / "stage_b_report.md"

BEHAVIOUR_LABEL = {"answer": "answer", "refuse": "refuse", "clarify": "clarify"}


def fmt_score(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--output", type=Path, default=REPORT)
    args = parser.parse_args(argv)
    data = json.loads(args.results.read_text(encoding="utf-8"))
    cfg, summary, turns = data["config"], data["summary"], data["turns"]
    section_map = cfg.get("section_to_article", {})
    if not section_map and any(
        label.startswith("KB") for turn in turns for label in turn["retrieved_sections"]
    ):
        # Older artifacts lack the mapping. Resolve from the same ingest corpus,
        # without retrieval or model calls; new artifacts retain their run mapping.
        sys.path.insert(0, str(REPO))
        from scripts.smoke_eval_retrieval import section_to_article_map

        section_map = section_to_article_map()

    def display_label(label: str) -> str:
        sections = sorted({
            section for section, article in section_map.items()
            if article == label and not section.startswith("Appendix ")
        })
        if sections:
            return f"{label} (§{' / §'.join(sections)})"
        return f"{label} (section unknown)" if label.startswith("KB") else label
    refusals = [t for t in turns if t["behaviour"] != "answer"]

    lines: list[str] = []
    profile = cfg.get("evaluation_profile", "DeepEval built-in metrics")
    lines.append(f"# Stage B evaluation report — {profile}")
    lines.append("")
    lines.append(
        f"_Generated {datetime.now(UTC):%Y-%m-%d %H:%M UTC} from "
        f"`{args.results.name}` (do not hand-edit)._"
    )
    lines.append("")
    lines.append("## Run configuration")
    lines.append("")
    lines.append("| Setting | Value |")
    lines.append("|---|---|")
    lines.append(f"| Collection | `{cfg['collection']}` |")
    lines.append(f"| Answer generator | `{cfg['gen_model']}` (grounded-QA prompt) |")
    lines.append(f"| Judge | `{cfg['judge_model']}` via Sprints LiteLLM proxy |")
    lines.append(f"| Metric threshold | {cfg['threshold']} |")
    for name, threshold in sorted(cfg.get("metric_thresholds", {}).items()):
        lines.append(f"| {name} threshold | {threshold} |")
    lines.append(f"| Retrieval | hybrid_reranked, top-{cfg['top_k']} |")
    lines.append("")
    if cfg.get("evaluation_profile"):
        lines.append(
            "Scores come from one joint rubric judgment per turn. They are not "
            "the built-in DeepEval Faithfulness/G-Eval scores and should not be "
            "compared directly with the previous profile."
        )
        lines.append("")
    if data.get("provenance_note"):
        lines.append(data["provenance_note"])
        lines.append("")
    usage = data.get("usage")
    if usage:
        observed = usage.get("observed_proxy_spend_usd")
        lines.append(
            f"- Observed proxy spend: {'unknown' if observed is None else f'${observed:.4f}'}"
        )
        lines.append(
            f"- Accounted spend (includes configured-rate estimates/reservations): "
            f"${usage['accounted_spend_usd']:.4f}; budget ${usage['budget_usd']:.2f}."
        )
        lines.append(
            f"- API attempts: {usage['llm_calls_total']}; "
            f"{usage['llm_calls_this_run']} in this invocation."
        )
        lines.append("")
    if data.get("status") == "stopped":
        lines.append(f"**Incomplete run:** {data.get('stop_reason')}")
        lines.append("")

    lines.append("## Summary")
    lines.append("")
    verdicts = summary["verdicts"]
    total = sum(verdicts.values())
    lines.append(
        f"- **{verdicts.get('PASS', 0)}/{total} turns passed** all applicable metrics "
        f"at the configured thresholds"
    )
    if "scored_turns" in summary:
        lines.append(
            f"- Scored turns: {summary['scored_turns']}/{data['target_turns']}; "
            f"execution errors: {verdicts.get('ERROR', 0)}."
        )
    lines.append(
        f"- Behaviour accuracy (answered when expected, refused when expected): "
        f"**{summary['behaviour_accuracy']:.0%}**"
    )
    lines.append("")
    lines.append("| Metric | Avg score | Pass rate |")
    lines.append("|---|---|---|")
    for name, st in summary["per_metric"].items():
        lines.append(f"| {name} | {st['avg_score']:.3f} | {st['pass_rate']:.0%} |")
    lines.append("")
    if summary.get("retrieval"):
        lines.append("## Retrieval label diagnostics")
        lines.append("")
        lines.append(
            "These are deterministic section/article-label overlap scores from the adapter, "
            "not DeepEval's semantic ContextualPrecision/ContextualRecall algorithms. "
            "Refusal and clarification turns are excluded from these averages."
        )
        lines.append("")
        for name, value in summary["retrieval"].items():
            lines.append(f"- Average {name}: {fmt_score(value)}")
        lines.append("- MRR averages reciprocal rank: first expected document at rank 1 = 1, rank 2 = 0.5, absent = 0. Refusal/clarification turns are excluded; MRR does not gate answer verdicts.")
        lines.append("")
    if summary.get("metric_coverage"):
        lines.append("## Metric coverage")
        lines.append("")
        for name, method in summary["metric_coverage"].items():
            lines.append(f"- {name}: {method}")
        lines.append("")

    lines.append("## Per-capability pass rates")
    lines.append("")
    lines.append("| Capability | Passed | Rate |")
    lines.append("|---|---|---|")
    caps = summary.get("capability_pass_rates", {})
    for cap, st in sorted(caps.items(), key=lambda kv: kv[1]["rate"]):
        lines.append(f"| {cap} | {st['passed']}/{st['total']} | {st['rate']:.0%} |")
    if caps:
        lines.append("")

    lines.append("## Per-turn results")
    lines.append("")
    metric_names = list(summary["per_metric"].keys())
    header = "| Turn | Behaviour | Diff | " + " | ".join(metric_names) + " | Verdict |"
    lines.append(header)
    lines.append("|" + "---|" * (3 + len(metric_names) + 1))
    for t in turns:
        scores = " | ".join(fmt_score(t["metrics"].get(m, {}).get("score")) for m in metric_names)
        lines.append(
            f"| {t['turn_id']} | {BEHAVIOUR_LABEL.get(t['behaviour'], t['behaviour'])} "
            f"| {t['difficulty']} | {scores} | {t['verdict']} |"
        )
    lines.append("")

    failing = [t for t in turns if t["verdict"] in ("FAIL", "ERROR")]
    lines.append(f"## Failing turns ({len(failing)})")
    lines.append("")
    if not failing:
        lines.append("_None._")
    for t in failing:
        bad = [f"**{name}** ({v['score']})" for name, v in t["metrics"].items() if not v["success"]]
        lines.append(f"### {t['turn_id']} — {t['behaviour']}, {t['difficulty']}")
        lines.append(f"- Query: {t['query']}")
        if t.get("error"):
            lines.append(f"- Execution error: {t['error']}")
        lines.append(f"- Failing metrics: {', '.join(bad) or 'behaviour mismatch'}")
        if not t["behaviour_ok"]:
            lines.append(
                f"- Behaviour mismatch: expected `{t['behaviour']}`, "
                f"agent {t.get('actual_behaviour', 'refused' if t['refusal_detected'] else 'answered')}"
            )
        for name, v in t["metrics"].items():
            if not v["success"] and v.get("reason") and not v["reason"].startswith("error"):
                lines.append(f"- {name} judge: {v['reason'][:300]}")
        lines.append(f"- Answer excerpt: “{t['answer'][:220]}…”")
        lines.append("")

    lines.append("## Refusal / clarification behaviour")
    lines.append("")
    lines.append(
        f"- {len(refusals)} refuse/clarify turns; agent refused in "
        f"{sum(1 for t in refusals if t['refusal_detected'])} of them."
    )
    traps = [t for t in refusals if not t["behaviour_ok"]]
    if traps:
        lines.append(f"- Behaviour mismatches: {', '.join(t['turn_id'] for t in traps)}")
    elif data.get("status", "complete") == "complete" and not verdicts.get("ERROR"):
        lines.append("- No refusal/clarification behaviour mismatches among scored turns.")
    lines.append("")

    lines.append("## Turn inspection")
    lines.append("")
    lines.append("Missing expected labels are retrieval diagnostics; forbidden retrieval never determines the verdict. Judge explanations are model assessments, not independently verified root causes. Retrieval uses the standalone question, not a history-based rewrite.")
    lines.append("")
    for t in turns:
        section_map = t.get("evaluation_source", {}).get("section_to_article", cfg.get("section_to_article", section_map))
        lines.append(f"### {t['turn_id']} — {t['verdict']}")
        lines.append("")
        source = t.get("evaluation_source")
        if source:
            lines.append(f"**Evaluation source:** `{source['artifact']}`; corpus `{source['corpus_version']}`; generation token limit {source['gen_max_tokens']}.")
            lines.append("")
            if t.get("previous_verdict"):
                lines.append(f"**Reingestion retest:** {t['previous_verdict']} → {t['verdict']}. Earlier result retained in the original artifact.")
                lines.append("")
        lines.append(f"**User question:** {t.get('user_input', t['query'])}")
        lines.append("")
        lines.append(f"**Retrieval query:** {t['query']}")
        lines.append("")
        lines.append(f"**Expected behaviour:** {t['behaviour']}; **judge-classified behaviour:** {t.get('actual_behaviour', 'unavailable')}; **behaviour check:** {'pass' if t['behaviour_ok'] else 'fail'}.")
        lines.append("")
        lines.append("**Expected answer:**")
        lines.append("")
        lines.append(t.get("reference", "Not stored in this older artifact."))
        lines.append("")
        lines.append("**Actual answer:**")
        lines.append("")
        lines.append(t['answer'] or "No answer produced.")
        lines.append("")
        retrieval = t.get("retrieval", {})
        expected = [label for label in retrieval.get("expected_labels", t['expected_sections']) if label != "—"]
        groups = retrieval.get("expected_label_groups", {})
        missing = sorted(label for label in expected if not set(groups.get(label, [label])).intersection(t['retrieved_sections']))
        lines.append(f"**Expected sections:** {', '.join(t['expected_sections']) or 'none'}; **mapped labels:** {', '.join(display_label(label) for label in expected) or 'none'}.")
        lines.append("")
        if groups:
            lines.append("**Appendix matches:** " + "; ".join(f"{label}: any of {', '.join(display_label(child) for child in children)}" for label, children in groups.items()) + ".")
            lines.append("")
        lines.append(f"**Retrieved labels (rank order):** {', '.join(display_label(label) for label in t['retrieved_sections']) or 'none'}; **missing labels:** {', '.join(display_label(label) for label in missing) or 'none'}.")
        lines.append("")
        lines.append(f"**Label recall:** {fmt_score(retrieval.get('recall'))}; **label precision:** {fmt_score(retrieval.get('precision'))}; **reciprocal rank:** {fmt_score(retrieval.get('reciprocal_rank'))}. These differ from semantic judge scores.")
        lines.append("")
        if missing:
            lines.append("**Inspection lead:** Expected documents were absent from the retrieved labels. Inspect the evidence below before attributing the failure to generation or judging.")
            lines.append("")
        if t.get("turn_criteria"):
            lines.append(f"**Turn-specific rubric:** {t['turn_criteria']}")
            lines.append("")
        lines.append("| Metric | Score | Threshold | Result | Judge explanation |")
        lines.append("|---|---|---|---|---|")
        for name, metric in t['metrics'].items():
            reason = str(metric.get('reason', '')).replace('|', '&#124;').replace('\n', '<br>')
            lines.append(f"| {name} | {fmt_score(metric.get('score'))} | {metric.get('threshold', cfg['threshold'])} | {'PASS' if metric['success'] else 'FAIL'} | {reason} |")
        lines.append("")
        if t.get("error"):
            lines.append(f"**Execution error:** {t['error']}")
            lines.append("")
        lines.append("<details><summary>Retrieved evidence and expected reference passages</summary>")
        lines.append("")
        for rank, hit in enumerate(t.get("retrieved_contexts", []), 1):
            lines.append(f"#### Retrieved {rank}: {display_label(hit.get('section', '?'))} — {hit.get('title', '')}")
            lines.append("")
            lines.append(hit.get('chunk_text', 'No text stored.'))
            lines.append("")
        lines.append("#### Expected reference passages")
        lines.append("")
        for context in t.get("reference_contexts", []):
            lines.append(context if isinstance(context, str) else json.dumps(context, ensure_ascii=False))
            lines.append("")
        lines.append("</details>")
        lines.append("")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
