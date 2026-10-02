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
    lines.append(f"| Retrieval | hybrid_reranked, top-{cfg['top_k']} |")
    lines.append("")
    if cfg.get("evaluation_profile"):
        lines.append(
            "Scores come from one joint rubric judgment per turn. They are not "
            "the built-in DeepEval Faithfulness/G-Eval scores and should not be "
            "compared directly with the previous profile."
        )
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
        f"at threshold {cfg['threshold']}"
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

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
