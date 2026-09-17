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
- **LLM** — **Gemini through the Sprints LiteLLM proxy**, the programme's mandated
  model path (`LITELLM_BASE_URL`, per-learner `LITELLM_API_KEY`, only `gemini/…` names
  allowed, $10/day). The default model is `gemini/gemini-3.5-flash`, the fastest of the
  models tested. The proxy speaks the OpenAI API, so the client is the official `openai`
  SDK: `chat.completions.parse` with a Pydantic `response_format` gives schema-validated
  output. The cost LiteLLM reports in `x-litellm-response-cost` is recorded on every
  generation. Error handling:
  - rate limits, 408, 5xx and connection errors become `RetryableError`;
  - other 4xx, content-filter refusals, truncation and schema failures become
    `TerminalError`.

  S2.3's retry policy therefore applies unchanged. Embeddings stay local (FastEmbed),
  because S1.4 built the index with them.
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
| webhook receipt and enqueue (API), after the bearer token is accepted | unauthenticated requests (checked before any span opens) |
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
- **Patterns:** bearer and basic credentials, JWTs, `sk-`/`pk-` provider keys (LiteLLM,
  Langfuse, Anthropic), GitHub, AWS and Slack keys, PEM private keys, `user:pass@` URLs, `password=` pairs, e-mail addresses and
  phone numbers of 8 or more digits.

Incident numbers, sys_ids and timestamps pass through unchanged. The same redaction runs
on incident text before it reaches the model, with a length bound (manual §11.6).

**Verified secret scan** — `tests/test_tracing.py::TestSecretScan`:

1. Runs the full graph on an incident whose description contains a password, a bearer
   JWT, an API key, an e-mail address and a phone number.
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
5. **The model's label and the incident's category can disagree.** In the live run,
   Gemini labelled the VPN failure `access` (corpus category `inquiry`), while KB0001 is
   filed under `network`. Retrieval therefore searches the model's category *and* the
   incident's own ServiceNow category (`search_categories`).
6. **Corpus gap — needs an owner decision.** KB0004 (print queues), KB0007 (endpoint),
   KB0008 (SAP) and KB0010 (order service) are tagged `restricted`. S1.4's default
   audience (#45) is `internal`, because the draft lands in a field every support user
   can read. Every hardware incident therefore escalates today. Raising
   `AGENT_MAX_SECURITY_LEVEL` or re-tagging the articles is a decision for S1.4 and the
   reviewer, not for this task.
7. **The "vague email" case passes the gate.** The W0.3 set expects the agent to *ask*
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

To run against real services, put `LITELLM_BASE_URL`, `LITELLM_API_KEY`,
`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_BASE_URL`, plus the S1.2
integration-user credentials, in `.env`. Then:

```bash
uv run python scripts/seed_qdrant.py                       # corpus → local Qdrant
uv run python scripts/run_agent_live.py --scenario vpn     # real Gemini + Qdrant + Langfuse
uv run python scripts/run_agent_live.py --number INC00…    # also real ServiceNow
just worker                                                # or the whole pipeline via the webhook
```

## 8. Live runs (2026-09-17)

`scripts/run_agent_live.py --scenario …` ran each case with real Gemini
(`gemini/gemini-3.5-flash` via LiteLLM), the real corpus in Qdrant and real Langfuse
Cloud. ServiceNow was in-memory for these first runs; the real-ServiceNow runs follow. Every trace arrived in Langfuse (read back through
`GET /api/public/v2/observations`) with the incident number as its session.

| Scenario | Outcome | Path | Spans | Model time | Cost (LiteLLM) | Trace |
|---|---|---|---|---|---|---|
| INC0010023 VPN | `suggested`, 5 cited steps from KB0001 §Resolution, confidence 0.95 | all 11 nodes | 17 | 15.4 s | $0.0269 | `b3e121cd81da22ab7c8595ed55aa8987` |
| INC0010064 MFA on identity | `suggested`, awaiting approval (Tier 1 + MFA reset), KB0006 | all 11 nodes | 17 | 23.7 s | $0.0495 | `7c8e97a3c0c5380dcfb662e0149e3757` |
| INC0010052 P1 order-processing | `escalated_high_risk` | load → validate → classify → determine_risk → act | 9 | 3.3 s | $0.0032 | `09f9b2980649a8ff0c8dc0cd2d5f5d00` |
| INC0010047 printer | `escalated_no_evidence` (no hardware article at `internal`) | … → retrieve → act | 10 | 3.4 s | $0.0026 | `7905ad8c9865a92830ad9b4b2bfde2e2` |
| W0.3 leave request | `escalated_no_evidence` (label `other`, no search) | … → retrieve → act | 10 | 2.7 s | $0.0027 | `10100e82a815acabce9422c6d1a7f782` |

### Real ServiceNow (PDI `dev434590`, integration user `ai_orchestrator_svc`)

An admin created four AI-enabled test incidents; the admin login was used for that
setup only. The agent read and wrote them as the non-admin integration user, with
OAuth. Reading the records back as admin confirmed the following:

- every write is attributed to `ai_orchestrator_svc`;
- Human Review Required = true and the state is `awaiting_approval`;
- the incident's own state is unchanged (New);
- there are no customer comments, only work notes.

| Incident | How it ran | Outcome on the record | Trace |
|---|---|---|---|
| INC0010022 VPN after password reset | `run_agent_live.py` | 5-step draft from KB0001 §Resolution, confidence 0.95, model `gemini/gemini-3.5-flash`, version `s2.5-graph-1.0.0` | `e9b06a0524e2b312a6304503f4505573` |
| INC0010023 P1 order-processing | `run_agent_live.py` | no draft; work note "risk assessed as high before retrieval — Priority 1 …" | `79faed09b14a3ca1423328cd9ccc8497` |
| INC0010024 printer grinding | `run_agent_live.py` | no draft; work note "Searched published hardware articles: nothing matched." | `343eed699ff290d84a13796c31ec3349` |
| **INC0010025 Outlook disconnected** | **full pipeline** (below) | 5-step draft from KB0002 §Resolution, confidence 0.90 | **`15d029ef3b0b58eec7ac4a76a7443eee`** |

### Full pipeline, one trace

INC0010025 ran the complete path:

1. **Webhook:** `POST /api/v1/webhook/incident` (uvicorn) returned 202, and a
   duplicate post returned 202 with `idempotent_replay: true`.
2. **Queue:** the event went to Redis.
3. **Worker:** the Celery worker (`AGENT_GRAPH_BACKEND=langgraph`) ran the graph with
   Postgres checkpoints and finished with `succeeded` in 19.2 s.

Postgres holds one `events` row, the execution (`succeeded`, `node_reached=act`), and
13 `workflow_state` rows (`__input__`, `__start__`, the 11 nodes, and `act` as
`awaiting_approval`).

Langfuse holds **one trace with 20 observations**, session `INC0010025`, in this order:

- `webhook.receipt` → `queue.enqueue` → the duplicate's `webhook.receipt`;
- `worker.pickup` (19.1 s);
- `node.load` with `servicenow.read_incident`;
- `node.validate`;
- `node.classify` with `llm.classify` (4.3 s);
- `node.determine_risk`;
- `node.retrieve` (0.19 s);
- `node.diagnose` with `llm.diagnose` (2.9 s);
- `node.generate` with `llm.generate` (9.1 s);
- the three gates;
- `node.act` with `servicenow.write_ai_fields`.

Model cost for the whole run: $0.032. The correlation id travelled from the HTTP
header, through the Celery message header, to the worker.

Screenshots of the Langfuse UI, captured 2026-09-17:

- [`evidence/langfuse-full-pipeline-INC0010025.png`](evidence/langfuse-full-pipeline-INC0010025.png):
  the single webhook-to-ServiceNow trace;
- [`evidence/langfuse-vpn-draft-INC0010022.png`](evidence/langfuse-vpn-draft-INC0010022.png): the
  cited draft;
- [`evidence/langfuse-p1-escalation-INC0010023.png`](evidence/langfuse-p1-escalation-INC0010023.png):
  risk HIGH, with no retrieval or generation spans.

The only key visible in them is the Langfuse *public* key, which is not a secret.

**Secret scan of what Langfuse actually stored.** All 137 observations stored in the
project, read back with inputs, outputs and metadata, were checked against the ten real
credential values in use:

- LiteLLM key;
- Langfuse secret;
- both ServiceNow passwords and client secrets;
- webhook token;
- Postgres and Redis passwords.

None appears. No e-mail address appears either, and the redaction markers are present.

What the numbers mean:

- **Latency:** a full draft takes 15–25 s of model time, inside the 90 s p95 budget
  (NFR-02). Escalating a P1 costs one classification call.
- **Budget:** at $0.03–0.05 per full run, the $10/day key covers roughly 200–350 full
  runs a day.
