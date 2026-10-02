# Budgeted BARQ evaluation

`deep_eval.py` makes one grounded answer request and one joint rubric judgment
per turn (200 successful requests for 100 turns). The generator still defaults
to the production agent model; the judge defaults to
`gemini/gemini-3.5-flash-lite`. The proxy must expose that model. Unavailable
models fail without falling back to a more expensive model.

This profile replaces the previous DeepEval built-in Faithfulness/G-Eval chain.
It reads the dataset's per-turn DeepEval recommendations and global G-Eval
rubrics. Answerable turns score faithfulness, answer relevance, semantic context
precision/recall, grounding, completeness, citations, over-refusal and safety.
Refusal turns score hallucination-free behavior, refusal quality and safety.
Applicable turn-specific criteria are included. Refusal and clarification are
distinct behaviors. These scores are **not directly comparable** to the old
built-in metrics. Retrieval recall, precision and forbidden sections are also
recorded through `adapters.score_retrieval()`, with manual section labels mapped
to KB article IDs before comparison. `must_not_retrieve` matches are diagnostics
only and never change the verdict or judge metrics. Label recall/precision are separate from semantic context
scores, and are not applicable to refusal/clarification turns. Missing mappings
stop evaluation rather than reporting misleading zero recall.

The report explicitly marks session-level ConversationalGEval/RoleAdherence and
the alternative RAGAS framework as not run. Standalone questions do not validate
conversation memory. Gold references and dataset criteria are left unchanged.

## Running and reporting

These commands make paid API calls; run them only when ready:

```sh
.venv/bin/python eval/deep_eval.py --limit 5
.venv/bin/python eval/deep_eval.py
```

The default output is `eval/deepeval_budget_results.json`, with a separate
`eval/deepeval_budget_results.checkpoint.json`. Existing
`eval/deepeval_results.json` and `eval/stage_b_report.md` are preserved. Generate
the new report without API calls:

```sh
.venv/bin/python eval/generate_stage_b_report.py \
  --results eval/deepeval_budget_results.json \
  --output eval/stage_b_budget_report.md
```

Exit codes: 0 = all turns passed, 1 = scored failures, 2 = incomplete execution
(budget, provider or response error). An incomplete run reports scored coverage
and execution errors separately rather than treating errors as quality failures.

## Budget and pricing

Default limits are $0.80 accounted spend, 220 API attempts, 768 generation output
tokens, 1,536 judge output tokens, and one request at a time. Generation uses low
reasoning effort. The Flash-Lite judge also uses low reasoning effort; optional
reasoning overrides are exposed. Output limits are sent as
`max_completion_tokens` and include reasoning tokens where the provider supports
that accounting. SDK retries are disabled; there are no automatic metric retries.

The budget persists **across invocations sharing a checkpoint**, including model
or rubric changes. Cached requests are reusable after reaching the allowance.
Each request reserves estimated maximum cost before dispatch. A proxy cost header
replaces the reservation; otherwise returned usage is charged to the configured
token rates. Missing headers report observed spend as **unknown**, not zero. A
timeout or ambiguous provider failure retains its reservation. A process lock
prevents two CLI runs from spending against the same checkpoint concurrently.

The fallback rates below are estimates, **not verified Sprints proxy prices**:

| Request | Input USD / million | Output USD / million |
|---|---:|---:|
| Production generator | 2.00 | 15.00 |
| Flash-Lite judge | 0.30 | 2.50 |

Generation estimates are deliberately conservative; judge estimates use Google's
[published Flash-Lite pricing](https://ai.google.dev/gemini-api/docs/pricing).
Supply verified proxy rates with `--gen-input-rate`, `--gen-output-rate`,
`--judge-input-rate` and `--judge-output-rate`, especially when changing models.
The guard can stop before all 100 turns finish. An absolute real-dollar limit
requires matching proxy rates or a provider-side budget; $0.80 estimated spend
cannot guarantee the proxy charged less than $1. No paid run was used to estimate
or validate the new profile's actual spend or judge quality.

## Reusing paid work

Raw responses are saved immediately, and answers plus retrieved evidence are
saved before judging. Rerunning identical settings makes no additional LLM
requests. Changing the threshold rescales cached judgments locally; changing
judge model/rubrics reuses generated answers. Budget counters and report metadata
are independent of cache keys, fixing the old resume config mismatch.

Dataset, generator prompt/model/token settings, history, retrieval URL/collection,
top-k and `--corpus-version` participate in generation cache keys. **Change
`--corpus-version` after re-ingesting or changing the collection's contents** to
avoid evaluating stale evidence. Cached malformed or truncated responses fail
again without automatically spending: inspect them, then change
`--judge-revision` to retry judging, or increase the relevant token limit. A judge
revision reuses generation and counts the new judgment against the same allowance.

The new cache does not import old built-in-metric results: those artifacts do not
contain complete retrieved text and cannot safely seed this profile's judgments.

## Verification without paid calls

```sh
.venv/bin/python -m pytest tests/test_deep_eval_budget.py -q
.venv/bin/python eval/deep_eval.py --help
```

Tests mock retrieval and all model requests; they cover call counts, budget stops,
restart/cache reuse, errors, schema validation, and report compatibility.

## Thresholds and judge rules

The general threshold remains 0.7. Scores use anchors: 1 = fully satisfied,
0.7 = only minor issues, 0.5 = substantial omissions/errors, 0 = failure.
Material fabrication, wrong citations and safety violations score zero on the
affected rubric. A valid refusal is assessed by meaning, not exact wording.

Override individual thresholds without making new paid calls when cached
judgments already exist:

```sh
.venv/bin/python eval/deep_eval.py --limit 5 \
  --metric-threshold faithfulness=0.9 --metric-threshold safety=1.0
```

Changing a threshold cannot repair an incorrectly classified refusal. New
prompt/rubric versions invalidate cached judgments but reuse generated answers.
The current judge defaults to Gemini 3.5 Flash-Lite with low reasoning effort;
2.5 Flash-Lite was unavailable to this account during the smoke run. The default
collection comes from retrieval settings (`incident_knowledge_base` by default).

## Inspecting a run

The completed 100-turn custom budget evaluation is in `eval/rag_eval_results.json`
and `eval/rag_eval_report.md`. The final batch's checkpoint is
`eval/rag_eval_final_batch_results.checkpoint.json`; earlier batch artifacts retain
their names for provenance. These are custom joint-rubric results, not built-in
DeepEval metric scores.

To run a fresh 100-turn evaluation against the current collection, use a new
output/checkpoint name so historical results remain available:

```sh
.venv/bin/python eval/deep_eval.py --collection incident_knowledge_base \
  --limit 100 --corpus-version manual-v4-reingested \
  --gen-max-tokens 1536 --budget-usd 1.00 --max-calls 220 \
  --output eval/rag_eval_current_results.json
.venv/bin/python eval/generate_stage_b_report.py \
  --results eval/rag_eval_current_results.json --output eval/rag_eval_current_report.md
```

This command makes paid calls. Repeat the same command to resume from its
checkpoint; change the corpus version after another reingestion. The dollar
limit may stop an incomplete run. The existing `rag_eval_results.json` combines
earlier batches and two targeted retests; it is not the output of one fresh run.
To regenerate its report without paid calls:

```sh
.venv/bin/python eval/generate_stage_b_report.py \
  --results eval/rag_eval_results.json --output eval/rag_eval_report.md
```

Retrieval diagnostics include MRR (mean reciprocal rank) over answerable turns.
Each turn scores `1 / rank` of its first expected section/article, or zero when
none is retrieved. Refusal/clarification turns are excluded. This costs no model
calls and does not change the answer pass/fail verdict.

The JSON stores the original question, retrieval query, expected answer/passages,
ranked retrieved passages, actual answer, and each metric's score, threshold,
and explanation. Generate a full inspection report without model calls:

```sh
.venv/bin/python eval/generate_stage_b_report.py \
  --results eval/smoke_budget_5_results.json --output eval/smoke_budget_20_report.md
```

Expand each turn's evidence to investigate missing documents, incomplete answers,
or questionable judge scores. Label overlap and semantic rubric scores measure
different things. Forbidden retrieval remains diagnostic and never gates a verdict.
Appendix labels such as `Appendix C` map to the corpus's `C` identifier.

For a saved truncated generation, `--gen-retry-turn S04-T1` explicitly retries
that turn with twice the generation token allowance. Earlier turns keep their
cache; later turns in the same session may change with the new answer. Preserve
this flag when resuming that retry. The original failed call remains in the cost
ledger. Do not raise token limits for every turn just to repair one truncation.
