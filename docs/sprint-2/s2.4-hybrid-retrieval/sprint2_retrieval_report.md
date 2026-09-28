# Sprint 2 Retrieval Report — Hybrid Search, Filtering & Reranking (S2.4)

Generated from `eval/ablation_results.json` -- seed `42`, collection `incident_knowledge_base`, top_k `5`. Regenerate with `uv run python eval/generate_report.py` after any change to the eval set, corpus, or retrieval code -- do not hand-edit the tables below.

> **This file is a generated snapshot of one ablation run, not a spec.** The prose below is
> emitted by `eval/generate_report.py` and is left exactly as generated. One reference in
> it has since gone stale: `retrieve_knowledge` was replaced by `hybrid_search` in #110, and
> the `internal` default is now `DEFAULT_MAX_SECURITY_LEVEL` in
> `src/app/retrieval/filters.py`. Read `hybrid_retrieval_spec.md` for current behaviour.

**Security levels, both reported on purpose:** this ablation ran at `restricted`, the level the eval set needs so restricted articles (KB0010, MIR-2026-03) are reachable at all. The application itself defaults to `internal` (`AGENT_MAX_SECURITY_LEVEL` in `.env.example`, enforced by `retrieve_knowledge`). The two differ by design: widen the app's level only per-incident, never to make this table's numbers look better.

## Summary

On this run of 23 incidents, hybrid-plus-reranked gains accuracy but loses context precision: context precision -0.0552, context recall +0.0000, accuracy +0.0345 versus dense-only. Rank movement after reranking: 0 incident(s) improved, 1 regressed, 22 unchanged. Latency is the deciding cost: hybrid + reranked p50 71.16 ms against 4.42 ms for dense-only (16.1x), while hybrid without the reranker sits at 4.53 ms. That is why the shipped default `RETRIEVAL_MODE` is `hybrid` -- it takes the sparse recall win (see §5: 1 sparse-rescue case(s) in this run) without paying for a cross-encoder that moved no answer into reach. Select `hybrid_reranked` per-request only where the extra latency budget exists. See §5 for whether this run's data satisfies the sparse-rescue requirement, and §7 for the duplicate-`article_id` decision.

## 1. Comparison Table

| Metric | Dense-Only | Hybrid | Hybrid + Reranked |
|---|---|---|---|
| context_precision | 0.5655 | 0.5552 | 0.5103 |
| context_recall | 0.7586 | 0.7931 | 0.7586 |
| accuracy | 0.7586 | 0.7931 | 0.7931 |
| hit_at_1 | 0.913 | 0.913 | 0.913 |
| hit_at_5 | 0.9565 | 1 | 1 |

## 2. Margin Over Dense-Only Baseline (hybrid + reranked)

- **context_precision_margin**: -0.0552
- **context_recall_margin**: +0.0000
- **accuracy_margin**: +0.0345

## 3. Latency Profile (milliseconds)

| Mode | p50 | p95 | mean |
|---|---|---|---|
| Dense-Only (baseline) | 4.42 | 5.18 | 4.48 |
| Hybrid (dense + sparse, RRF) | 4.53 | 5.38 | 4.64 |
| Hybrid + Reranked (cross-encoder) | 71.16 | 76.26 | 71.33 |

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
| SYN_SPARSE_1 | 1 | 1 | 0 |
| SYN_SPARSE_2 | 1 | 1 | 0 |
| SYN_SPARSE_3 | 1 | 1 | 0 |
| SYN_SPARSE_4 | 4 | 5 | -1 |

## 5. Sparse-Rescue Cases (dense-only failed, hybrid succeeded)

> **Requirement gap**: this run found only 1 sparse-rescue case(s). The scope of work requires at least two concrete queries where the sparse component fixes a dense-only failure.
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
