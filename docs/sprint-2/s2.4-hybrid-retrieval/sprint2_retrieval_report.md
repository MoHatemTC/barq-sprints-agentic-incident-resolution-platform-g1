# Sprint 2 Retrieval Report — Hybrid Search, Filtering & Reranking (S2.4)

Generated from `eval/ablation_results.json` -- seed `42`, collection `incident_knowledge_base`, top_k `5`, max_security_level `restricted`. Regenerate with `uv run python eval/generate_report.py` after any change to the eval set, corpus, or retrieval code -- do not hand-edit the tables below.

## Summary

Hybrid-plus-reranked outperforms the dense-only baseline on context recall and overall accuracy, consistent with NFR-08. The cross-encoder correctly refuses out-of-KB incidents that dense-only's cosine threshold let through, at the cost of a substantial latency increase, which should be weighed against the platform's latency headroom NFR before defaulting production traffic to `hybrid_reranked`. See §5 for whether this run's data satisfies the sparse-rescue requirement, and §7 for the duplicate-`article_id` decision.

## 1. Comparison Table

| Metric | Dense-Only | Hybrid | Hybrid + Reranked |
|---|---|---|---|
| context_precision | 0.608 | 0.588 | 0.768 |
| context_recall | 0.76 | 0.76 | 0.96 |
| accuracy | 0.76 | 0.76 | 1.0 |
| hit_at_1 | 0.9474 | 0.9474 | 0.9474 |
| hit_at_5 | 1 | 1 | 1 |

## 2. Margin Over Dense-Only Baseline (hybrid + reranked)

- **context_precision_margin**: +0.1600
- **context_recall_margin**: +0.2000
- **accuracy_margin**: +0.2400

## 3. Latency Profile (milliseconds)

| Mode | p50 | p95 | mean |
|---|---|---|---|
| Dense-Only (baseline) | 51.93 | 85.45 | 61.87 |
| Hybrid (dense + sparse, RRF) | 45.91 | 54.4 | 46.77 |
| Hybrid + Reranked (cross-encoder) | 666.81 | 818.63 | 681.3 |

## 4. Cross-Encoder Rank Movement (hybrid -> hybrid+reranked)

| Incident | Hybrid Rank | Reranked Rank | Movement |
|---|---|---|---|
| INC0010023 | 1 | 1 | 0 |
| INC0010064 | 1 | 1 | 0 |
| INC0010052 | 1 | 1 | 0 |
| INC0010024 | 1 | 1 | 0 |
| INC0010025 | 1 | 1 | 0 |
| INC0010026 | 1 | 1 | 0 |
| INC0010027 | 2 | 2 | 0 |
| INC0010028 | 1 | 1 | 0 |
| INC0010029 | 1 | 1 | 0 |
| INC0010031 | 1 | 1 | 0 |
| INC0010033 | 1 | 1 | 0 |
| INC0009884 | 1 | 1 | 0 |
| INC0010091 | 1 | 1 | 0 |
| INC0010092 | 1 | 1 | 0 |
| INC0010096 | 1 | 1 | 0 |
| INC0010097 | 1 | 1 | 0 |
| INC0010098 | 1 | 1 | 0 |
| INC0010099 | 1 | 1 | 0 |
| INC0010100 | 1 | 1 | 0 |

## 5. Sparse-Rescue Cases (dense-only failed, hybrid succeeded)

> **Requirement gap**: this run found only 0 sparse-rescue case(s). The scope of work requires at least two concrete queries where the sparse component fixes a dense-only failure.
>
> This is not evidence the hybrid path is unneeded -- it means the current eval set doesn't contain a query shaped to need it. The sparse leg exists for verbatim token matching (error codes, KB numbers, exact identifiers) that dense embeddings tend to smooth over. Remediation before sign-off:
>
> 1. Add incidents to `eval/evaluation_set.json` that describe a symptom in paraphrased, non-technical language but whose correct article is only identifiable via an exact token -- e.g. a query built around `RFC_ERROR_COMMUNICATION` that never says "SAP" or "RFC" directly, or a query referencing `KB0010` / `MIR-2026-03` by ID rather than by symptom.
> 2. Re-run `uv run python eval/ablation.py` and confirm `sparse_rescue_cases` has >= 2 entries where dense-only actually misses and hybrid actually hits.
> 3. Re-run this script to regenerate this section with the real cases -- do not write example cases in by hand.

## 6. Reproducibility Notes

- **Seed**: `42` (currently no sampling occurs anywhere in the harness, so this is inert but recorded for a future subsampling mode).
- **Dense model**: `BAAI/bge-small-en-v1.5` (384-dim, cosine).
- **Sparse model**: `Qdrant/bm25` (FastEmbed); IDF applied server-side by Qdrant via `modifier="idf"` on the sparse vector.
- **Fusion**: Reciprocal Rank Fusion (`Fusion.RRF`) over dense + sparse prefetches, each capped at `max(fetch_limit * 4, 20)` candidates; ties broken deterministically by `(article_id, chunk_index)`.
- **Reranker**: FastEmbed `TextCrossEncoder`, model from `RetrievalSettings.rerank_model`; truncates fused candidates to top_k=5.
- **Point IDs**: deterministic UUIDv5 (`KB_NAMESPACE`), so re-seeding never changes retrieval identity between runs.
- **Modes evaluated**: dense_only, hybrid, hybrid_reranked.
- **Low-confidence refusal thresholds**: {'dense_only': 0.3, 'hybrid': 0.05, 'hybrid_reranked': 0.0}.

## 7. Duplicate `article_id` Handling

Confirmed intentional, not an oversight: `context_precision` is chunk-level by design (project scoring contract in `eval/ablation.py`'s `metric_definitions`), and returning multiple chunks from one correct article gives the generator a denser, more useful context window than padding with one chunk each from marginally-relevant articles. No dedup is applied in `hybrid_search.py`; keep this documented rather than implicit, since it affects how `context_precision` should be read.
