# Incident pipeline defects: BUG-001–BUG-012

This change follows the actual implementation on main `41cfbc4`, rather than
copying proposed fixes from the issue descriptions. Some reported paths and
examples do not match that implementation. Existing features are retained and
covered by tests; confirmed failures are repaired at their owning boundaries.

| Issue | Confirmed behavior and change |
|---|---|
| #192 — semantic screening | An unavailable classifier previously passed input. Timeout, exception and malformed output now withhold processing. The audit distinguishes screening failure from a positive injection verdict. |
| #193 — false positives | Bare `developer mode`, `new instructions:` and long certificate/base64 blobs are legitimate incident content. These broad rules are narrowed or removed; actual instruction overrides, encoded execution instructions and control delimiters remain screened. JSON, links and punctuation were not blanket-banned in the current code. |
| #194 — article bundling | An already-selected out-of-category article's Resolution sibling must retain publication/security filters, not the initial category restriction. Qdrant scroll returns unscored records; these now validate correctly. Version matching, chunk deduplication and the final capacity remain enforced. |
| #195 — noisy queries | A successful rewrite now supplies a focused query instead of appending the noisy original again. Literal codes/versions and explicit negated clauses are retained deterministically. The original remains the timeout/invalid-output fallback and is already part of the incident audit. No model-selected action is executed by rewriting. |
| #196 — discarded reranking | Cross-encoder scores now control final article/chunk ordering in reranked mode, including bundling. Dense cosine remains a separately named evidence gate: a sigmoid score is not a calibrated probability, and reusing the cosine threshold for it would be unsafe. |
| #197 — citation identity | Diagnosis, generation and verification share evidence-based identity resolution. A base article number resolves only when one retrieved version exists. Unknown explicit versions and ambiguous base IDs stay invalid. Section spelling/case aliases resolve only to a section actually present in that article's retrieved evidence. |
| #198 — diagnostic matching | The prompt explicitly accepts grounded issue descriptions from Cause or Resolution; a literal Symptoms heading is unnecessary. Topic-only matches remain insufficient. The mismatch penalty is retained for a genuine lack of symptom support. |
| #199 — lost tail steps | Generation keeps every grounded step. A redundant source footer may be omitted, but procedure steps are never chopped to fit. Overflow fails verification and requests a bounded revision; if revision fails, the full draft remains in the interrupt audit for review. Oversized drafts cannot be accepted without a fitting operator solution. |
| #200 — approval deadlock | A paused draft approved without replacement text is applied from the checkpoint and completes. A pre-retrieval pause has no draft: empty approval returns 409 without recording a decision or consuming the pause. An operator can supply a resolution or reject it. Straight-through drafts already have the `/suggestions/{execution_id}/decide` route; that acceptance path completes the incident and remains tested. No automatic-write setting exists in the checked implementation, and none is invented here. |
| #201 — rejection outcomes | A rejected paused execution closes PostgreSQL as failed/cancelled with a human-rejection cause. Both decision routes clear stale AI suggestion/resolution text and record the refusal in ServiceNow. A human-supplied resolution still completes the incident even when the AI draft was rejected. |
| #202 — tool authorizations | Unscoped workflow approval rows no longer invalidate a valid approval for the requested tool. Only matching execution/tool decisions grant permission. The newest matching rejection/revocation still wins; tied or malformed matching decisions fail closed. |
| #203 — DLQ visibility | The authenticated DLQ inspection API and operator replay already existed. Redis inspection failure now returns 503 instead of an empty successful list. Records expose execution ID, original correlation ID and failure type; PostgreSQL failure details retain a redacted, bounded traceback. Replay carries the original trace context and only removes snapshotted old records after enqueue. Enqueue failure keeps DLQ visibility and restores the parked DB state/budget/timestamp only if no worker claimed it; ServiceNow is marked failed again on a best-effort basis. Historical failure rows are retained. |

The field model's 4,000-character suggestion limit remains unchanged. An overflow
never becomes a truncated runnable procedure. Approval briefs remain descriptive;
the operator's structured decision, not the brief, drives resumption.

## Verification

Local gates: Ruff lint/format and mypy passed. The unit run passed 1,426 tests;
its 41 database-dependent cases were then run separately and all passed. All
22 integration tests and 62 real-PDF/manual parser tests passed. OpenAPI matches
the application. No test or CI gate was disabled.

Automated tests cover benign technical input and attacks, fail-closed classifier
failures, real in-memory Qdrant sibling filtering, version/section identity,
procedure overflow, original-trace replay, enqueue failure, matching tool
revocation, rejection clearing, empty approvals and checkpointed draft acceptance.
`docs/evidence/diagnosis_section_regression.json` also records real Gemini
diagnosis using only Cause and only Resolution chunks from the live Qdrant
article; both identify the relevant issue without a Symptoms chunk.

PostgreSQL integration tests additionally verify replay rollback against the
actual retry-state constraints and refusal to cancel a running worker.

The live reproducer is `scripts/verify_incident_bug_regressions_live.py`. It creates
isolated fixtures on Ali's PDI and uses an isolated local database. Admin access
is used only for fixture setup; the FastAPI/Celery pipeline authenticates as the
integration service account. It reads the private workspace env and publishes
only identifiers, HTTP/status results and field lengths. It never deploys EC2.

`docs/evidence/pr191_live_approval_regressions.json` records the real stack:
FastAPI → Redis/Celery → LangGraph/PostgreSQL → Qdrant/Gemini → ServiceNow.
One run approves the complete checkpointed draft without a replacement solution;
the other refuses an empty pre-retrieval approval, then rejects the same paused
execution. ServiceNow reaches `complete` and `failed` respectively, PostgreSQL
agrees, exactly one approval row exists per execution and duplicate decisions
return 409. `pr191_trace_continuity.json` independently reads Langfuse and
records 32 and 17 observations respectively under each original trace ID,
including worker and resumed ServiceNow write spans. No UI screenshot or EC2
verification is claimed by these artifacts.

Retrieval quality and limitations are documented separately in
`retrieval_query_rewrite_and_reranking.md`, with original and synthetic-noise
ablation artifacts. The new manual/OCR corpus still needs mapped ground truth
before those results can be generalized to it.

## Dependency PR assessment

The current GitHub mapping is #185 Langfuse 4.15.6, #186 OpenAI 3.19.2,
#187 Ruff 0.16.9 and #188 CodeQL action 4.38.2. Their Python/integration checks
passed; #188's CodeQL checks passed. The failed ServiceNow SDK job was inspected:
it fails on the existing brace-expansion high-severity advisory. The compatible
lockfile patch is already included in this PR; no threshold is weakened.

The three Python patches were also exercised together against the changed unit
suite in a temporary uv environment. They were not folded into this PR's lockfile
or merged. The bot branches need to incorporate the main lockfile repair, rerun
all checks, and receive current-head review before merge. Source releases:
[Langfuse](https://github.com/langfuse/langfuse-python/releases),
[OpenAI](https://github.com/openai/openai-python/releases/tag/v3.19.2),
[Ruff](https://github.com/astral-sh/ruff/releases/tag/0.16.9),
[CodeQL action](https://github.com/github/codeql-action/releases/tag/v4.38.2).
