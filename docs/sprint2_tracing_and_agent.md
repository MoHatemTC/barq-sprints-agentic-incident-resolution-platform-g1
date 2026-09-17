# Sprint 2 · S2.5 — Langfuse tracing and agent runtime

Covers FR-11, FR-13 and FR-19. The graph design and the risk-ordering record are in
[`sprint3_graph_design.md`](sprint3_graph_design.md); the picture is
[`graph_state_diagram.png`](graph_state_diagram.png).

## 1. What runs where

| Brief path | Repository path | Notes |
|---|---|---|
| `src/observability/tracing.py` | `src/observability/tracing.py` | plus `redaction.py` |
| `src/agent/state.py` | `src/agent/state.py` | |
| `src/agent/llm.py` | `src/agent/llm.py` | |
| `src/agent/checkpointer.py` | `src/agent/checkpointer.py` | backed by S2.2's `workflow_state` |
| `src/agent/graph.py`, `src/agent/nodes/` | same | one module per node |
| `src/workers/tasks.py` | `src/app/workers/tasks.py` | S2.3's worker lives in the `app` package; the seam is `invoke_graph` |

Supporting modules: `agent/config.py` (settings), `agent/policy.py` (eligibility and
risk rules), `agent/edges.py` (edge conditions), `agent/retrieval.py` (the S2.4 seam),
`agent/servicenow.py` (the permitted-action gateway over the S1.5 client),
`agent/dependencies.py` and `agent/runtime.py` (providers and per-process bootstrap).

```
webhook (API)                    Redis                     Celery worker
─────────────                    ─────                     ─────────────
webhook.receipt ──┐
                  └ queue.enqueue ── x_correlation_id ──▶ worker.pickup
                                     header                ├ node.load ─ servicenow.read_incident
                                                           ├ node.validate
                                                           ├ node.classify ─ llm.classify
                                                           ├ node.determine_risk
                                                           ├ node.retrieve
                                                           ├ node.diagnose ─ llm.diagnose
                                                           ├ node.generate ─ llm.generate
                                                           ├ node.verify_evidence
                                                           ├ node.safety_check
                                                           ├ node.confidence_check
                                                           └ node.act ─ servicenow.write_ai_fields
```

## 2. Runtime initialisation

- **Settings** — `AgentSettings` (`AGENT_*`) and `TracingSettings` (`LANGFUSE_*`,
  `TRACING_*`). Defaults for search and safety are the values in the BARQ manual §11.7:
  `top_k=5`, `threshold=0.55`, `floor=0.45`, `risk_p=[1]`. `.env.example` lists them all.
- **Providers** — `get_llm()`, `get_embedding_engine()`, `get_tracer()`,
  `get_agent_dependencies()` and `get_runtime()` are `lru_cache` singletons. Nothing is
  built at import, so each prefork child creates its own clients after the fork, once.
  Tests inject every dependency through `AgentDependencies`.
- **LLM** — Claude (`claude-opus-5`) through the official `anthropic` SDK:
  `beta.messages.parse` with a Pydantic output schema, adaptive thinking, effort `high`
  and the server-side refusal fallback (`fallbacks="default"`). Rate limits, 5xx and
  connection errors become `RetryableError`; other 4xx, refusals, truncation and
  missing structured output become `TerminalError`, so S2.3's retry policy applies
  unchanged.
- **ServiceNow** — the S1.5 async client runs on one private event loop per worker
  process, so its HTTP pool and OAuth token cache survive across tasks. Every call goes
  through `IncidentGateway`, which refuses anything outside the manual's permitted
  actions (`read_incident`, `search_knowledge`, `write_work_note`, `flag_human_review`,
  `write_ai_fields`).
- **Checkpointer** — `AGENT_CHECKPOINTER_BACKEND=postgres` stores checkpoints in
  `workflow_state` (§5). `memory` is for tests.
- **Worker** — `AGENT_GRAPH_BACKEND=langgraph` (default) runs the graph;
  `stub` keeps S2.3's simulated graph for its failure-injection demo and integration suite.

## 3. Tracing

### Correlation

The webhook's `X-Correlation-ID` (or a generated UUID) is the one identifier. The
producer sends it as the `x_correlation_id` message header, not a task argument, so the
task signature and the replay CLI are unchanged. The Langfuse trace id is
`Langfuse.create_trace_id(seed=correlation_id)`, so the API process and the worker
emit into the same trace with nothing else shared. A message without the header (a
replay) is traced under its execution id.

Every span carries the incident number and execution id. The trace's `session_id` is
the incident number, and its tags are `incident:<number>` and `execution:<id>`, so a
trace can be found from either key. Model calls are Langfuse *generations*. Each one
records the model that actually served the request, input and output tokens, cost (from
list prices, so Langfuse can total it) and the prompt name and version.

### Boundaries — what is and is not traced

| Traced | Not traced |
|---|---|
| webhook receipt and enqueue (API) | authentication failures before the handler runs |
| worker pickup, one span per attempt, with the attempt number | Celery's internal broker traffic |
| one span per node, in execution order | Postgres checkpoint writes (the rows are their own audit trail) |
| every ServiceNow call, with its permitted-action name and risk class | ServiceNow response bodies (only `ok` / error type) |
| every model call, as a generation | the raw chain of thought (never returned by the API) |
| node inputs as the list of state sections present, node outputs redacted | full incident text inside node *inputs* |

### Secrets and personal data

`observability/redaction.py` is the Langfuse `mask` function, so it runs on every input,
output and metadata value before export. It applies two layers:

- **Keys:** anything under a secret-looking key (`password`, `client_secret`,
  `authorization`, `token` and similar) is replaced.
- **Patterns:** bearer and basic credentials, JWTs, Anthropic, Langfuse, GitHub, AWS and
  Slack keys, PEM private keys, `user:pass@` URLs, `password=` pairs, e-mail addresses and
  phone numbers of 8 or more digits.

Incident numbers, sys_ids and timestamps pass through unchanged. The same redaction runs
on incident text before it reaches the model, with a length bound (manual §11.6).

**Verified secret scan** — `tests/test_tracing.py::TestSecretScan`:

1. Runs the full graph on an incident whose description contains a password, a bearer
   JWT, an Anthropic key, an e-mail address and a phone number.
2. Exports the spans through the real Langfuse client into memory.
3. Asserts that none of those strings appear in any span attribute or event, in any
   model prompt, or in the JSON logs. The Langfuse secret key and ServiceNow credentials
   are checked too. The test also asserts that redaction markers are present, so the scan
   did see the incident text.

### Tracing never breaks an execution

Every Langfuse call goes through `Tracer`, which swallows and counts its own failures
and logs them once, without the exception text. Exceptions raised by the traced code
still propagate unchanged. **Induced failure tests**
(`tests/test_tracing.py::TestInducedFailure`) run the same incident with each of these
tracing failures and assert the result is identical to a run with tracing off:

- span creation fails;
- span update and span close fail;
- the exporter raises;
- Langfuse is unreachable (real OTLP exporter pointed at a closed port);
- the client cannot be constructed;
- trace-attribute propagation fails.

Missing keys mean tracing is off, not an error.

## 4. Measurements

### Tracing overhead

`uv run python scripts/measure_tracing_overhead.py --runs 500`, on an Apple Silicon
laptop, Python 3.12. The graph runs with mocked model and ServiceNow calls, so the
numbers show what tracing adds:

| Mode | Runs | p50 ms | p95 ms | p50 overhead |
|---|---|---|---|---|
| off | 500 | 1.87 | 2.14 | — |
| real Langfuse client, in-memory export, flushed every run | 500 | 4.97 | 5.58 | +3.10 ms |

That is **17 spans per execution at about 0.18 ms each**, including masking and a
synchronous flush. The worker does not flush per task: export is batched on a background
thread and flushed on process shutdown, so a real run pays less than this. Against a
real execution, which makes three model calls taking seconds each and has a 90 s p95
budget (NFR-02), tracing costs under 0.1%. The webhook adds two spans: an estimated ~0.4 ms
at that per-span cost (not measured separately), against S2.3's measured 22.7 ms p95
for the 202. With Langfuse keys set, the script also
measures the real export path.

### Evidence gate calibration

`uv run python scripts/calibrate_retrieval_threshold.py` ingests the 11-article corpus
(45 chunks) with `BAAI/bge-small-en-v1.5`. It then scores each case's dense cosine under
the agent's filters (published only, category, security tier).

| Incident | Expected | Best cosine (internal) | Gate | Best cosine (restricted allowed) | Gate |
|---|---|---|---|---|---|
| INC0010023 VPN after password reset | answer | 0.909 | pass | 0.909 | pass |
| Outlook disconnected | answer | 0.770 | pass | 0.770 | pass |
| Account locked | answer | 0.773 | pass | 0.773 | pass |
| Mapped drive missing | answer | 0.788 | pass | 0.788 | pass |
| Wi-Fi drops on 5 GHz | answer | 0.740 | pass | 0.740 | pass |
| W0.3 "cannot send email" (vague) | ask | 0.728 | pass | 0.728 | pass |
| INC0010047 printer grinding (mechanical) | escalate | — | escalate | 0.708 | pass |
| W0.3 printer after office move | answer | — | escalate | 0.782 | pass |
| W0.3 annual leave request | escalate | — | escalate | — | escalate |
| Coffee machine leaking | escalate | — | escalate | 0.594 | pass |
| Excel VLOOKUP question | escalate | 0.437 | escalate | 0.437 | escalate |

What this shows, and what the code does about it:

1. **RRF scores cannot gate evidence.** They measure rank, not relevance: every query
   has a rank-1 hit. The gate therefore uses the dense cosine of the same chunks under
   the same mandatory filter.
2. **Labels with no corpus category have no evidence by definition.** An unfiltered
   search scored the leave request at 0.58, above the manual's threshold. `security` and
   `other` now skip the search and escalate.
3. **Similarity cannot tell a mechanical printer fault from a queue fault** (0.708
   against KB0004). That is the `diagnose` node's job: the model must name an article
   whose *symptom* matches. When it names none, the graph escalates before generating
   anything (the `diagnose → act` edge).
4. **With these rules, the manual's 0.55 separates every case in the sample.**
5. **Corpus gap — needs an owner decision.** KB0004 (print queues), KB0007 (endpoint),
   KB0008 (SAP) and KB0010 (order service) are tagged `restricted`. S1.4's default
   audience (#45) is `internal`, because the draft lands in a field every support user
   can read. Every hardware incident therefore escalates today. Raising
   `AGENT_MAX_SECURITY_LEVEL` or re-tagging the articles is a decision for S1.4 and the
   reviewer, not for this task.
6. **The "vague email" case passes the gate.** The W0.3 set expects the agent to *ask*
   for detail. "Ask" is not an outcome in this sprint; a low-confidence diagnosis sends
   it to `escalated_low_confidence`.

## 5. Checkpoints in `workflow_state`

One row per checkpoint, which means one row per completed node, plus the `__input__`
and `__start__` bootstrap rows:

| Column | Value |
|---|---|
| `node_name`, `attempt`, `sequence_number` | the node, the Celery delivery attempt, and the write order |
| `status` | `started` (bootstrap), `succeeded`; for `act`: `awaiting_approval` (draft written), `blocked` (escalated), `skipped` |
| `decision` | the routing verdict: eligibility, risk, retrieval summary, confidence, outcome |
| `evidence` | citations with relevance and fused score (`retrieve`); sources (`generate`) |
| `state_snapshot` | LangGraph's typed checkpoint, metadata, versioned channel blobs, pending writes |

`executions.node_reached` is updated as nodes complete. Storage follows LangGraph's
reference `InMemorySaver`: each row keeps only the channels that changed at its step,
and a read rebuilds the full state from the whole thread.

**Resume (NFR-03).** `tests/test_checkpointer.py` (integration, real Postgres):

1. Makes `generate` fail on attempt 1.
2. Runs attempt 2 with a fresh saver instance.
3. Asserts the run resumes at `generate`: `classify` and `diagnose` are not paid for
   again, ServiceNow is read once and written once.
4. Redelivers attempt 3 and asserts nothing runs again.

A node re-run under the same attempt number (after a hard kill) replaces its row
instead of violating `uq_workflow_state_execution_node_attempt`.

## 6. ServiceNow write-back (manual §11.1, S1.1 field model)

`act` sends a single PATCH containing:

- the AI fields;
- an internal work note (never `comments`);
- **Human Review Required = true**.

The processing state is `awaiting_approval`, both for a draft and for an escalation:
S1.1 reserves `complete` for an *applied* resolution, and that state requires
`ai_resolution` in the same write. Processing end stays blank for the same reason.
Other details:

- **Work notes** follow the wording of the manual's run log, for example "AI Suggested
  Response: risk assessed as high before retrieval — Priority 1 … No action taken.
  Escalated for human decision."
- **Human Lock** set between the read and the write means nothing is written, and the
  outcome is `skipped_human_lock`.
- **`AGENT_WRITE_BACK_ENABLED=false`** is a dry run: the outcome is recorded but nothing
  is written.

## 7. How to run

```bash
uv run pytest tests/test_nodes.py tests/test_graph.py tests/test_agent_bootstrap.py  # the brief's single command
uv run pytest                                    # everything that needs no services
docker compose up -d postgres redis qdrant
uv run pytest -m integration                     # checkpointer on Postgres + S2.3 suite
uv run python scripts/measure_tracing_overhead.py
uv run python scripts/calibrate_retrieval_threshold.py
uv run python scripts/render_graph_diagram.py    # regenerates graph_state_diagram.png
```

To run the full pipeline, set `ANTHROPIC_API_KEY` (or use an `ant auth login` profile)
and `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL`, plus the S1.2
integration-user credentials. Then start the worker (`just worker`) and post an event
to the webhook.
