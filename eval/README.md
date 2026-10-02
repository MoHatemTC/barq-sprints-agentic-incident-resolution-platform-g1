# Budgeted BARQ evaluation

`deep_eval.py` makes one grounded answer request and one joint rubric judgment
per turn (200 successful requests for 100 turns). The generator still defaults
to the production agent model; the judge defaults to
`gemini/gemini-2.5-flash-lite`. The proxy must expose that model. Unavailable
models fail without falling back to a more expensive model.

This profile replaces the previous DeepEval built-in Faithfulness/G-Eval chain.
It scores grounding, completeness, citations, over-refusal and safety together,
including applicable turn-specific criteria. Refusal and clarification are
distinct behaviors. These scores are **not directly comparable** to the old
built-in metrics. Retrieval recall, precision and forbidden sections are also
recorded with deterministic checks.

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
reasoning effort. The Flash-Lite judge uses its default (thinking off); optional
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
| Flash-Lite judge | 0.10 | 0.40 |

Generation estimates are deliberately conservative; judge estimates use Google's
[published Flash-Lite pricing](https://ai.google.dev/gemini-api/docs/pricing#gemini-2.5-flash-lite).
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
