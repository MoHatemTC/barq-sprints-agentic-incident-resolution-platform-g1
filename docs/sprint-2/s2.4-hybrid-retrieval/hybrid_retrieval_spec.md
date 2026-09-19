# S2.4 Hybrid Retrieval — Developer Guide

**Sprint**: Sprint 2 (S2.4) — Hybrid Retrieval: Fusion, Metadata Filtering, Reranking & Baseline Measurement
**Status**: Implemented; one acceptance gap open (see §8)
**Read this before touching**: `src/retrieval/hybrid_search.py`, `src/retrieval/filters.py`, `src/retrieval/rerank.py`, `eval/ablation.py`

This is the reference doc for anyone extending, debugging, or auditing the retrieval
layer built in this sprint. `sprint2_retrieval_report.md` (auto-generated, same
directory) is the *measurement* artifact for one specific ablation run — treat it as
a snapshot, not a spec. This document is the spec.


## 1. What this sprint built, and why

Sprint 1 (S1.4) populated a single Qdrant collection (`incident_knowledge_base`) with
45 chunks from 11 real BARQ operations-manual runbooks, each carrying a dense vector
(`bge-small-en-v1.5`), a sparse vector (`Qdrant/bm25`), and 7 keyword-indexed metadata
fields. Sprint 1 did not query it — it only built and verified the index.

Sprint 2 (this sprint) builds the **query-side** engine on top of that collection:

1. Combine dense + sparse search into one fused candidate list (FR-14).
2. Enforce metadata pre-filtering that cannot be bypassed by a caller (FR-14, and the
   P3 safety invariant carried over from Sprint 1's `retrieve_knowledge`).
3. Re-score fused candidates with a cross-encoder before generation sees them.
4. Prove, with a real ablation run on a versioned eval set, that hybrid+reranked beats
   dense-only by a *measured* margin (NFR-08) — not an asserted one.
5. Keep all three modes (dense-only / hybrid / hybrid+reranked) switchable via config
   with zero code changes, so the falsifiability requirement in NFR-08 actually holds
   at runtime, not just in a test.

Everything below explains how those five things are implemented and what to watch out
for when you change any of them.

---

## 2. Request lifecycle (end to end)

```
caller
  │
  ▼
hybrid_search() / timed_hybrid_search()      [src/retrieval/hybrid_search.py]
  │
  ├─ 1. Resolve mode: explicit `mode=` arg > RetrievalSettings.retrieval_mode
  ├─ 2. build_metadata_filter(metadata, extra)  [src/retrieval/filters.py]
  │      → always injects workflow_state + security_level conditions FIRST,
  │        caller's `extra` filter is appended AFTER, never replaces them
  ├─ 3. engine.embed_query(query)              [dense vector + sparse indices/values]
  │
  ├─ 4a. DENSE_ONLY  → client.query_points(query=dense_vec, using="dense", ...)
  │
  └─ 4b. HYBRID / HYBRID_RERANKED
         → client.query_points(
               prefetch=[Prefetch(dense, filter=F, limit=4*fetch_limit_or_20),
                         Prefetch(sparse, filter=F, limit=4*fetch_limit_or_20)],
               query=FusionQuery(fusion=Fusion.RRF),
               query_filter=F,          # defense-in-depth, same filter again
           )
  │
  ├─ 5. _validate_hit() per point → RetrievalHit (Pydantic, frozen, extra="forbid")
  │      Malformed payload → raises ValueError naming the point id. Never coerces.
  ├─ 6. _tie_break_sort(): -score, then article_id, then chunk_index
  │
  ├─ 7. if HYBRID_RERANKED and hits:
  │        CrossEncoderReranker.rerank(query, hits, top_n=limit)
  │        → re-scores every hit's `chunk_text` against `query`, replaces `.score`,
  │          re-sorts with the SAME tie-break, truncates to `limit`
  │    else:
  │        hits = hits[:limit]
  │
  └─ 8. return SearchResult(hits, latency_ms, mode)
```

Two things worth internalizing immediately:

- **The filter is applied three times when hybrid**: once per `Prefetch` (dense, sparse)
  and once more at the top-level `query_filter`. This is deliberate — it's the same
  P3 defense-in-depth pattern from Sprint 1's `retrieve_knowledge`, now generalized.
  If you refactor this to "simplify" by dropping the top-level filter, you reopen the
  candidate-starvation bug Qdrant Advisor flagged in S1.4.
- **Reranking only ever narrows, never widens, the candidate set.** `fetch_limit` for
  reranked mode is `max(limit * 4, settings.rerank_candidate_limit)` — the reranker
  sees more candidates than `limit` so it has room to promote a lower-ranked-but-more-
  relevant chunk, but it can only select from what fusion already surfaced. If fusion
  never retrieves the right chunk in its top `fetch_limit`, reranking cannot rescue it.

---

## 3. Metadata filtering (`src/retrieval/filters.py`)

### 3.1 The two conditions that are never optional

- `workflow_state` defaults to `["published"]` only. Retired/draft articles are
  invisible unless a caller explicitly overrides `MetadataFilterBuilder.workflow_state`
  — and even then, this is a builder field, not something an `extra: Filter` can force,
  because `extra` is appended to `must` **after** these two, never merged into them.
- `security_level` defaults to `DEFAULT_MAX_SECURITY_LEVEL = INTERNAL`, i.e.
  `["public", "internal"]`. `restricted` content (5 of 11 corpus articles) is invisible
  by default. This mirrors the #45 fix from Sprint 1 — before that fix, restricted
  articles leaked to every caller because `RetrievalHit` didn't even carry the field.

### 3.2 Cumulative security tiers, not exact match

`_allowed_security_levels(max_level)` returns every tier at-or-below `max_level` in
`SECURITY_LEVEL_ORDER = (PUBLIC, INTERNAL, RESTRICTED)`. Requesting `RESTRICTED` gets
you all three tiers, not just restricted-tagged articles. If a future requirement needs
"restricted-only" access (e.g. an audit view), that's a *different* filter shape and
should be a new builder field, not an overload of `max_security_level`.

---

## 4. Fusion mechanics

- **Method**: Reciprocal Rank Fusion (`Fusion.RRF`), delegated to Qdrant's native
  fusion support via `FusionQuery`. We do not hand-roll RRF math — Qdrant reconciles
  the dense (cosine) and sparse (BM25 dot-product) score scales internally, which is
  the "documented fusion method that explicitly reconciles score scales" the scope of
  work asks for. If you ever need to swap fusion methods, that's a one-line change to
  `FusionQuery(fusion=...)`, not a rewrite.
- **Tie-breaking**: `_tie_break_sort()` — descending score, then `article_id`
  alphabetically, then `chunk_index` ascending. This is applied identically after
  fusion and after reranking, so ordering is deterministic and reproducible across
  runs given the same corpus and query.
- **Candidate pool size before fusion**: each `Prefetch` requests
  `max(fetch_limit * 4, 20)` candidates per leg (dense and sparse separately) before
  RRF combines them. This is intentionally generous relative to `fetch_limit` so that
  a chunk which ranks well on only one of the two signals still has a chance to survive
  fusion.

---

## 5. Reranking (`src/retrieval/rerank.py`)

- **Model**: FastEmbed's `TextCrossEncoder`, model name from
  `RetrievalSettings.rerank_model` (overridable per-instance via
  `CrossEncoderReranker(model_name=...)`, mainly for tests).
- **Lazy load, process-wide cache**: the model is not loaded until the first
  `.rerank()` call (`_load()` checks `self._model is None`), and
  `get_default_reranker()` is `@lru_cache`d so the model loads at most once per
  process. If you're writing a script that calls this in a loop (like `ablation.py`
  does across 25 incidents × 3 modes), you want `get_default_reranker()`, not a fresh
  `CrossEncoderReranker()` per call, or you'll reload the model every time.
- **Scoring contract**: `model.rerank(query, documents)` returns one score per
  document, in the same order. `zip(hits, raw_scores, strict=True)` means a count
  mismatch between hits and scores raises immediately rather than silently
  misaligning — covered by `test_mismatched_score_count_raises`.
- **top_n=0 or negative is a caller error**, not "return everything" or "return
  nothing" — it raises `ValueError`. Covered by `test_top_n_zero_raises` /
  `test_top_n_negative_raises`.
- **Cross-encoder scores are not comparable to RRF scores.** `ablation.py`'s
  `compute_rerank_movement()` deliberately compares *rank position* of the first
  relevant article between hybrid and hybrid+reranked, not raw score deltas — don't
  add score-based comparisons between pre- and post-rerank hits anywhere; it's an
  apples-to-oranges comparison (cosine/BM25-fused score vs. cross-encoder logit).

---

## 6. Config and mode switching (`src/core/config.py`)

`RetrievalMode` is the enum with three values: `DENSE_ONLY`, `HYBRID`,
`HYBRID_RERANKED`. The active mode resolves as: explicit `mode=` argument to
`hybrid_search()` / `timed_hybrid_search()` **wins over** `RetrievalSettings.retrieval_mode`
(env-configured default). This is what makes "switchable via configuration with zero
code changes" true in production (flip the env var) while still letting the ablation
harness force all three modes in one run without touching config.

`get_retrieval_settings()` failures **must propagate**, not silently fall back to a
default collection name — see `test_settings_errors_stop_retrieval_instead_of_switching_collection`.
If you're tempted to wrap the settings call in a broad `try/except` "for robustness,"
don't: a broken `QDRANT_HTTP_PORT` env var should stop retrieval loudly, not silently
redirect it to whatever collection name happens to be hardcoded as a fallback.

---

## 7. Evaluation harness (`eval/ablation.py`, `eval/evaluation_set.json`)

### 7.1 Scoring contract

- **Ground truth per incident**: `primary_article_ids` (weight 1.0),
  `acceptable_article_ids` (weight 0.5), `forbidden_article_ids` (instant failure if
  any forbidden article appears in results, regardless of everything else).
- **`context_precision`** (chunk-level): mean relevance value across every returned
  hit. Returning 5 chunks from the one correct article scores higher than returning
  1 correct chunk + 4 irrelevant ones — this is intentional (§7 of the generated
  report), not a bug, because the generator downstream benefits from a denser correct
  context over token-padding with marginal articles. **Do not "fix" this by deduping
  article_ids before scoring** without updating the metric definition and re-running
  the whole ablation, or you'll silently change what the recorded margin means.
- **`context_recall`** (article-level, deduped): fraction of total ground-truth weight
  recovered among the *distinct* article_ids returned.
- **Unanswerable incidents**: scored correct if either no hits are returned, or the
  top hit's score is below `LOW_CONFIDENCE_THRESHOLDS[mode]`. These thresholds are
  **not comparable across modes** — `0.30` is a cosine-similarity threshold,
  `0.05` is post-RRF-fusion, `0.0` is a cross-encoder logit threshold. If you add a
  fourth mode, you need a new, independently-calibrated threshold; don't reuse one of
  the existing three by analogy.
- **`--seed` currently has zero runtime effect.** It's recorded in the output JSON for
  future reproducibility (a planned incident-subsampling mode), but nothing in
  `ablation.py` samples anything today. Don't assume changing `--seed` changes results
  — if you need actual determinism verification, rerun the whole eval set twice and
  diff `ablation_results.json`.
- **P3 safety net inside the harness itself**: `run_mode()` raises immediately if any
  returned hit has `workflow_state != "published"`, independent of whether the eval
  set's `forbidden_article_ids` happens to name that article. This is a second,
  harness-level enforcement of the same invariant `filters.py` and `test_search.py`
  already enforce — treat a failure here as more serious than a normal test failure,
  since it means retrieval leaked non-published content past three independent layers.

### 7.2 Running it

```bash
uv run python eval/ablation.py                          # default: limit=5, seed=42, max_security_level=restricted
uv run python eval/ablation.py --limit 5 --seed 42 \
    --max-security-level restricted \
    --output eval/ablation_results.json
uv run python eval/generate_report.py                    # renders docs/sprint-2/s2.4-hybrid-retrieval/sprint2_retrieval_report.md
```

`--max-security-level restricted` is the default in `ablation.py` deliberately —
several eval incidents (KB0010) are restricted-tier, so evaluating with the
retrieval-default `internal` ceiling would make those incidents unanswerable by
construction and corrupt the metrics. If you add new eval incidents referencing
restricted articles, this default matters; don't quietly narrow it.

---

## 8. Known gaps and findings as of the latest ablation run

These findings are based on the latest ablation run using seed 42, limit 5, and
`max_security_level=restricted`, after a deliberate multi-query investigation into
the sparse-rescue requirement. They should be re-verified whenever the evaluation
set, corpus, or retrieval implementation changes.

1. **One genuine sparse-rescue case is now confirmed.** `SYN_SPARSE_4`
   (`"RITM0010877 order service connection pool emergency change approval
   workflow"` → `KB0006-v3.0`) is absent from dense-only's top 5 entirely and is
   recovered by hybrid at rank 4. This is a strict rescue per
   `find_sparse_rescues()`: dense-only misses the article completely, hybrid's
   RRF fusion (not the reranker) recovers it. Verified directly against
   `ablation_results.json`'s `per_incident` data, not asserted from the summary
   alone.

2. **A second rescue was actively pursued and not found, across seven queries on
   two independent anchor tokens.** Both `MIR-2026-03` (targeting `KB0010-v2.0`)
   and `CHG0030455` (also `KB0010-v2.0`) were tried as bare tokens and in four
   further variants layering in vocabulary from competing articles at
   different intensities. None produced a strict dense-miss/hybrid-hit:
   - The bare-token and lightly-modified variants stayed inside dense's top 5
     (best case: rank 4, one slot from falling out).
   - Heavier variants pushed the target out of dense's top 5, but also pushed it
     out of the sparse/RRF candidate window — `hybrid` also came back null, not
     just dense.
   - One variant landed the target at rank 5 in `hybrid_reranked` only, which is
     explicitly *not* a sparse rescue by the harness's own definition (fusion
     never surfaced it; the cross-encoder recovered a candidate from a wider
     `fetch_limit` window instead). Recorded as a legitimate but distinct
     mechanism, not conflated with #1.
   - A second own-vocabulary anchor, `PRB0040018` (also targeting `KB0010-v2.0`,
     phrased around the article's own "pool drain procedure" language, mirroring
     what worked for `RITM0010877`), was tried as a further test and also did
     not produce a rescue.

3. **Working hypothesis for why `KB0010-v2.0` resists rescue where `KB0006-v3.0`
   did not**: with only 11 published articles in the corpus, "top 5" is
   effectively the top half. `KB0010-v2.0`'s dense embedding already sits in a
   less crowded semantic neighborhood (order-processing/database language is
   distinct from anything else in the corpus), so it tends to survive on dense
   signal alone even when a query is stripped down or restructured. `KB0006-v3.0`
   apparently sits closer to genuine semantic competitors (KB0005, KB0010's own
   approval-gate language), which is why an ID-plus-distractor query was able to
   knock it out of dense's top 5 in a way the same technique couldn't replicate
   for KB0010.

4. **Corpus-size constraint, restated with evidence rather than assumption**: the
   original gap note speculated that a suitably shaped query would find two
   rescues. That speculation has now been tested directly, not just reasoned
   about — one rescue is confirmed real, and a second was not found despite a
   systematic, evidence-directed search (not random guessing) across two
   plausible target articles. This is not proof no second rescue exists
   anywhere in the query space, but it is evidence that this corpus, at
   `limit=5`, may only reliably support one clean example without adding new
   knowledge content — which is out of scope per the constraint to keep the KB
   untouched.

5. **Hybrid improves first-hit ranking; reranking adds the largest quality gain
   at a substantial latency cost.** These findings from the prior run continue
   to hold and should be re-stated from the latest `ablation_results.json`
   after §1–2 above are reflected in the eval set (do not hand-copy old numbers
   forward — regenerate via `eval/generate_report.py`).

---

## 9. Test map

| File | Covers |
|---|---|
| `tests/retrieval/test_filters.py` | Pure filter construction (mandatory conditions, cumulative security tiers, list vs. single-value matching, extra-filter merge order, empty-list edge case) + planted-exclusion acceptance test proving filtering beats semantic relevance |
| `tests/retrieval/test_rerank.py` | Lazy loading, process-wide cache, top_n validation, tie-breaking, score replacement, mismatched-score-count failure, model name resolution |
| `tests/retrieval/test_search.py` | End-to-end hybrid_search: mode switching, dual-prefetch filter placement, malformed-payload handling, P3 KB0010-v1/v2 acceptance tests, #45 restricted-tier default exclusion + opt-in |
| `eval/ablation.py` (self-contained, not pytest) | Full 3-mode ablation against a live collection; run manually, not part of CI unit suite (needs a seeded Qdrant instance) |

When adding a new metadata field or a new retrieval mode, extend `test_filters.py`
or `test_search.py` respectively **and** add a corresponding eval-set incident if the
change affects what should or shouldn't be retrievable — a unit test proves the code
path works, only the ablation harness proves it changes the measured margin.

---

## 10. Quick command reference

```bash
# Set up / rebuild the collection (from Sprint 1, idempotent unless --force-recreate)
uv run python scripts/setup_qdrant.py

# Seed the 45 chunks (idempotent, verifies stored == upserted)
uv run python scripts/seed_qdrant.py

# Validate corpus + coverage matrix invariants
uv run python scripts/validate_corpus.py

# Run this sprint's full test suite
uv run pytest tests/retrieval/test_filters.py tests/retrieval/test_rerank.py tests/retrieval/test_search.py -v

# Run the ablation and regenerate the report
uv run python eval/ablation.py
uv run python eval/generate_report.py
```
```