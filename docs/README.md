# Documentation index

Documentation for a task normally lives in one folder per task, inside one folder
per sprint — `docs/sprint-N/sN.M-short-name/`, each with its own `README.md` naming
the owner, the tracking issues, and what belongs there. That is the layout
`docs/sprint-1/` follows throughout. It is not followed everywhere: under
`docs/sprint-2/` the S2.4 folder keeps the convention but S2.1's material sits in a
double-nested `sprint-2.1/` with the sprint segment hyphenated, and thirteen
Sprint 2 and Sprint 3 design records sit flat at the top level of `docs/` as
`docs/sprint2_*.md` and `docs/sprint3_*.md`, with no per-task folder at all. Both
layouts are indexed below, so a record is findable either way. See
[ROADMAP.md](ROADMAP.md) for the full four-sprint PRD scope, and
[../TEAM.md](../TEAM.md) for who owns what.

## Sprint 1 — Platform Build

[docs/sprint-1/](sprint-1/) — status table and links for all five tasks.

| Task | Owner |
|---|---|
| [S1.1 — Scoped app and field model](sprint-1/s1.1-scoped-app-and-field-model/) | [@ali-ezz](https://github.com/ali-ezz) |
| [S1.2 — Execution log and OAuth identity](sprint-1/s1.2-execution-log-and-oauth-identity/) | [@MohamedAbdelaiem](https://github.com/MohamedAbdelaiem) |
| [S1.3 — Eligibility rule and event](sprint-1/s1.3-eligibility-rule-and-event/) | [@ahmedtamer101](https://github.com/ahmedtamer101) |
| [S1.4 — Knowledge and Qdrant](sprint-1/s1.4-knowledge-and-qdrant/) | [@kerolos-mohsen](https://github.com/kerolos-mohsen) |
| [S1.5 — ServiceNow client](sprint-1/s1.5-servicenow-client/) | [@Tasneemmohammed0](https://github.com/Tasneemmohammed0) |

S1.1 holds the field dictionary, implementation runbook, acceptance matrix, PDI
verification records, submission checklist and screenshots. S1.2 holds the audit
and identity design with its verified permission matrix. S1.4 holds the corpus
design and index specification. S1.3 holds the event contract and its verification
evidence. S1.5 holds a `README.md` recording the client's behaviour and security
properties.

## Sprint 2 — Event Integration

Tracked in the [Sprint 2 milestone](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/milestone/2). Scope: the FastAPI webhook
with 202 semantics, the PostgreSQL state schema with database-level idempotency,
Redis and Celery with a dead-letter path, hybrid retrieval measured against a
dense-only baseline, and Langfuse tracing over an explicit LangGraph state machine.

| Task | Owner |
|---|---|
| S2.1 — FastAPI application, webhook and 202 semantics | [@MohamedAbdelaiem](https://github.com/MohamedAbdelaiem) |
| S2.2 — PostgreSQL state schema, migrations, idempotency | [@ahmedtamer101](https://github.com/ahmedtamer101) |
| S2.3 — Redis queue, Celery workers, dead-letter path | [@kerolos-mohsen](https://github.com/kerolos-mohsen) |
| S2.4 — Hybrid retrieval, filtering, reranking, baseline | [@Tasneemmohammed0](https://github.com/Tasneemmohammed0) |
| S2.5 — Langfuse tracing, agent init, LangGraph state machine | [@ali-ezz](https://github.com/ali-ezz) |
| S2.6 — RAG corpus hardening: OCR images, tables, multi-column layouts | [@Tasneemmohammed0](https://github.com/Tasneemmohammed0) |

Documentation is created as each task opens its first pull request. S2.4's material
is in `sprint-2/s2.4-hybrid-retrieval/`; S2.1's is in `sprint-2/sprint-2.1/`, which
does not follow the `sN.M-name/` convention. The remaining Sprint 2 design records
are the flat `docs/sprint2_*.md` files listed above.

> Note: [ROADMAP.md](ROADMAP.md) places hybrid retrieval and the state machine in
> Sprint 3. Both were pulled forward into Sprint 2 as S2.4 and S2.5, so the task
> table above is authoritative for sprint assignment and the roadmap remains the
> reference for overall PRD scope.

## Sprint 3 — Retrieval & Reasoning

Tracked in the [Sprint 3 milestone](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/milestone/3). Scope: multi-agent diagnosis and resolution, a server-side tool registry with permission classes, input and output guardrails, true LangGraph interrupt/resume with an approval audit trail and crash recovery, and the human-resolution knowledge-capture loop.

| Task | Owner | Design record |
|---|---|---|
| S3.1 — Multi-agent diagnosis and resolution | [@MohamedAbdelaiem](https://github.com/MohamedAbdelaiem) | [sprint3_multi_agent_design.md](sprint3_multi_agent_design.md) |
| S3.2 — Tool registry, permission classes and allowlist | [@ahmedtamer101](https://github.com/ahmedtamer101) | [sprint3_tool_registry.md](sprint3_tool_registry.md) |
| S3.3 — Input and output guardrails | [@Tasneemmohammed0](https://github.com/Tasneemmohammed0) | No design record in the repository yet |
| S3.4 — LangGraph interrupt/resume, approval audit, crash recovery | [@ali-ezz](https://github.com/ali-ezz) | [sprint3_hitl_design.md](sprint3_hitl_design.md), [sprint3_recovery_design.md](sprint3_recovery_design.md) |
| S3.5 — Human-resolution KB write-back and Qdrant re-ingestion | [@kerolos-mohsen](https://github.com/kerolos-mohsen) | [sprint3_knowledge_capture_design.md](sprint3_knowledge_capture_design.md) |

[sprint3_graph_design.md](sprint3_graph_design.md) is the graph-architecture and
risk-ordering record for the eleven-node state machine. It has no single owning task
above: the S2.5 brief named it, and it is indexed here because the graph is Sprint 3's
subject matter.

## Cross-sprint

| Document | Contents |
|---|---|
| [demo_runbook.md](demo_runbook.md) | How to drive the end-to-end demo yourself: prerequisites, the script, and the expected result of each phase |
| [ROADMAP.md](ROADMAP.md) | The full four-sprint PRD scope, not just what is built |
| [../TEAM.md](../TEAM.md) | Who owns which task, and the other project roles |

## Conventions

- Scope prefix for every Incident column: `x_2215032_ai_inc_0_ai_`
- Scripts use **internal** choice values (`in_progress`), never display labels
- No artifact may live in the ServiceNow Global scope
- The outbound event carries `event_id`, `sys_id`, `number` and `event_type` only —
  never the full incident record. The backend retrieves what it is authorised to
  retrieve, so ACLs remain the single source of truth and the contract survives
  new Incident fields
- New sprint documentation follows the `docs/sprint-N/sN.M-name/` folder layout when
  the task has more than a design record to hold, and a single flat
  `docs/sprint-N_<subject>.md` record at the top level of `docs/` when it does not.
  Either way it is indexed above, with a `README.md` naming the owner from the day
  the task is created — not added after the fact
