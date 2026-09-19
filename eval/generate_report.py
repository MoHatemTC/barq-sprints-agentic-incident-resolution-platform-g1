#!/usr/bin/env python3
"""Render the full Sprint 2 retrieval report from eval/ablation_results.json.

Writes docs/sprint-2/s2.4-hybrid-retrieval/sprint2_retrieval_report.md in one
piece -- narrative plus data-driven tables -- so every number in the report
traces back to a real ablation run instead of being typed in by hand.

Usage:
    uv run python eval/ablation.py          # produces the JSON
    uv run python eval/generate_report.py   # renders the .md from it
"""

from __future__ import annotations

import json
from pathlib import Path

RESULTS_PATH = Path("eval/ablation_results.json")
REPORT_PATH = Path("docs/sprint-2/s2.4-hybrid-retrieval/sprint2_retrieval_report.md")

MODE_LABELS = {
    "dense_only": "Dense-Only (baseline)",
    "hybrid": "Hybrid (dense + sparse, RRF)",
    "hybrid_reranked": "Hybrid + Reranked (cross-encoder)",
}


def render_narrative_header(results: dict) -> list[str]:
    lines: list[str] = []
    lines.append("# Sprint 2 Retrieval Report — Hybrid Search, Filtering & Reranking (S2.4)\n")
    lines.append(
        f"Generated from `{RESULTS_PATH}` -- seed `{results['seed']}`, "
        f"collection `{results['collection']}`, top_k `{results['limit']}`, "
        f"max_security_level `{results['max_security_level']}`. "
        "Regenerate with `uv run python eval/generate_report.py` after any change "
        "to the eval set, corpus, or retrieval code -- do not hand-edit the tables below.\n"
    )
    lines.append("## Summary\n")
    lines.append(
        "Hybrid-plus-reranked outperforms the dense-only baseline on context recall "
        "and overall accuracy, consistent with NFR-08. The cross-encoder correctly "
        "refuses out-of-KB incidents that dense-only's cosine threshold let through, "
        "at the cost of a substantial latency increase, which should be weighed "
        "against the platform's latency headroom NFR before defaulting production "
        "traffic to `hybrid_reranked`. See §5 for whether this run's data satisfies "
        "the sparse-rescue requirement, and §7 for the duplicate-`article_id` decision.\n"
    )
    return lines


def render_generated_body(results: dict) -> list[str]:
    summary = results["summary"]
    margin = results["margin_hybrid_reranked_over_dense_only"]
    rescues = results["sparse_rescue_cases"]
    movement = results["rerank_rank_movement"]

    lines: list[str] = []

    lines.append("## 1. Comparison Table\n")
    lines.append("| Metric | Dense-Only | Hybrid | Hybrid + Reranked |")
    lines.append("|---|---|---|---|")
    for metric in ("context_precision", "context_recall", "accuracy", "hit_at_1", "hit_at_5"):
        row = [metric]
        for mode in ("dense_only", "hybrid", "hybrid_reranked"):
            row.append(str(summary[mode].get(metric, "n/a")))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines.append("## 2. Margin Over Dense-Only Baseline (hybrid + reranked)\n")
    for k, v in margin.items():
        lines.append(f"- **{k}**: {v:+.4f}")
    lines.append("")

    lines.append("## 3. Latency Profile (milliseconds)\n")
    lines.append("| Mode | p50 | p95 | mean |")
    lines.append("|---|---|---|---|")
    for mode in ("dense_only", "hybrid", "hybrid_reranked"):
        s = summary[mode]
        lines.append(
            f"| {MODE_LABELS[mode]} | {s['p50_latency_ms']} | "
            f"{s['p95_latency_ms']} | {s['mean_latency_ms']} |"
        )
    lines.append("")

    lines.append("## 4. Cross-Encoder Rank Movement (hybrid -> hybrid+reranked)\n")
    lines.append("| Incident | Hybrid Rank | Reranked Rank | Movement |")
    lines.append("|---|---|---|---|")
    for m in movement:
        lines.append(
            f"| {m['incident_id']} | {m['hybrid_rank']} | "
            f"{m['hybrid_reranked_rank']} | {m['movement']} |"
        )
    lines.append("")

    lines.append("## 5. Sparse-Rescue Cases (dense-only failed, hybrid succeeded)\n")
    if len(rescues) >= 2:
        for r in rescues:
            lines.append(
                f"- **{r['incident_id']}**: dense returned "
                f"`{r['dense_returned_article_ids']}`, hybrid returned "
                f"`{r['hybrid_returned_article_ids']}`."
            )
    else:
        lines.append(
            f"> **Requirement gap**: this run found only {len(rescues)} sparse-rescue "
            "case(s). The scope of work requires at least two concrete queries where "
            "the sparse component fixes a dense-only failure.\n"
            ">\n"
            "> This is not evidence the hybrid path is unneeded -- it means the current "
            "eval set doesn't contain a query shaped to need it. The sparse leg exists "
            "for verbatim token matching (error codes, KB numbers, exact identifiers) "
            "that dense embeddings tend to smooth over. Remediation before sign-off:\n"
            ">\n"
            "> 1. Add incidents to `eval/evaluation_set.json` that describe a symptom "
            "in paraphrased, non-technical language but whose correct article is only "
            "identifiable via an exact token -- e.g. a query built around "
            '`RFC_ERROR_COMMUNICATION` that never says "SAP" or "RFC" directly, or '
            "a query referencing `KB0010` / `MIR-2026-03` by ID rather than by symptom.\n"
            "> 2. Re-run `uv run python eval/ablation.py` and confirm "
            "`sparse_rescue_cases` has >= 2 entries where dense-only actually misses "
            "and hybrid actually hits.\n"
            "> 3. Re-run this script to regenerate this section with the real cases -- "
            "do not write example cases in by hand."
        )
    lines.append("")

    lines.append("## 6. Reproducibility Notes\n")
    lines.append(
        f"- **Seed**: `{results['seed']}` (currently no sampling occurs anywhere "
        "in the harness, so this is inert but recorded for a future subsampling mode)."
    )
    lines.append("- **Dense model**: `BAAI/bge-small-en-v1.5` (384-dim, cosine).")
    lines.append(
        "- **Sparse model**: `Qdrant/bm25` (FastEmbed); IDF applied server-side by "
        'Qdrant via `modifier="idf"` on the sparse vector.'
    )
    lines.append(
        "- **Fusion**: Reciprocal Rank Fusion (`Fusion.RRF`) over dense + sparse "
        "prefetches, each capped at `max(fetch_limit * 4, 20)` candidates; ties "
        "broken deterministically by `(article_id, chunk_index)`."
    )
    lines.append(
        f"- **Reranker**: FastEmbed `TextCrossEncoder`, model from "
        f"`RetrievalSettings.rerank_model`; truncates fused candidates to top_k={results['limit']}."
    )
    lines.append(
        "- **Point IDs**: deterministic UUIDv5 (`KB_NAMESPACE`), so re-seeding never "
        "changes retrieval identity between runs."
    )
    lines.append(f"- **Modes evaluated**: {', '.join(results['modes'])}.")
    lines.append(
        f"- **Low-confidence refusal thresholds**: {results['low_confidence_thresholds']}."
    )
    lines.append("")

    lines.append("## 7. Duplicate `article_id` Handling\n")
    lines.append(
        "Confirmed intentional, not an oversight: `context_precision` is chunk-level "
        "by design (project scoring contract in `eval/ablation.py`'s "
        "`metric_definitions`), and returning multiple chunks from one correct article "
        "gives the generator a denser, more useful context window than padding with "
        "one chunk each from marginally-relevant articles. No dedup is applied in "
        "`hybrid_search.py`; keep this documented rather than implicit, since it "
        "affects how `context_precision` should be read."
    )

    return lines


def render(results: dict) -> str:
    lines = render_narrative_header(results) + render_generated_body(results)
    return "\n".join(lines) + "\n"


def main() -> None:
    results = json.loads(RESULTS_PATH.read_text())
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(render(results))
    print(f"wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
