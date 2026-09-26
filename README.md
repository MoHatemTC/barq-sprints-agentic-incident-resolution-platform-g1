# AI Incident Orchestrator

[![CI](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)
[![Sprint 1](https://img.shields.io/github/milestones/progress/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/1)](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/milestone/1)

Agentic incident resolution on ServiceNow: event-driven, observable, and guardrailed. A Business Rule inside a scoped ServiceNow application evaluates eligibility when an incident is created or meaningfully updated, then emits a minimal event carrying identifiers only. A FastAPI webhook authenticates it, validates the schema, checks idempotency and returns `202` without ever waiting on a model. Work is queued in Redis and executed by Celery workers running an explicit LangGraph state machine over hybrid retrieval from Qdrant, with operational state and audit in PostgreSQL and per-node tracing in Langfuse.

**Explicitly out of scope, by design:** polling of any kind, Kubernetes, and admin-credential authentication.

Built by BARQ × Sprints G1 across four one-week sprints. See [TEAM.md](TEAM.md) for who
owns what, and [docs/ROADMAP.md](docs/ROADMAP.md) for the full four-sprint scope — this
repository currently implements Sprint 1 only.

---

## Architecture

```
ServiceNow incident created / updated
          │
          ▼  Business Rule — six eligibility conditions
   minimal event {event_id, sys_id, number, event_type}
          │  RESTMessageV2, OAuth, identifiers only
          ▼
   FastAPI webhook ──► authenticate ──► validate ──► idempotency ──► 202 Accepted
          │                                                    (no model on this thread)
          ▼
   Redis queue ──► Celery worker ──► LangGraph state machine
                                        load → validate → classify → risk
                                        → retrieve → diagnose → generate
                                        → verify evidence → safety → confidence
                                        → act  |  interrupt for human approval
                                             │
                    ┌────────────────────────┼────────────────────────┐
                    ▼                        ▼                        ▼
              Qdrant (vectors only)   PostgreSQL (state,        Langfuse
              dense + sparse, fused   approvals, audit)         (per-node traces)
```

Retrieval and operational state are deliberately separate: Qdrant holds vectors and nothing else, PostgreSQL holds executions, idempotency keys, approvals and failures.

---

## Repository layout

| Path | Contents |
|---|---|
| `src/app/main.py` | FastAPI application entrypoint |
| `src/app/core/config.py` | Pydantic settings. ServiceNow fields are **required** so the service fails fast rather than booting half-configured |
| `src/app/core/constants.py`, `src/app/core/logging.py` | Shared constants and the structlog JSON logging configuration |
| `src/api/` | The API as it runs: `app.py` wiring, `routers/` (`webhook`, `executions`, `approvals`, `config`, `dlq`, `eval`, `health`), `schemas/`, bearer auth, correlation middleware and error handlers (S2.1, S2.4) |
| `src/app/api/` | Re-export of the webhook router onto the `app` package; `webhook.py` is a one-line shim onto `api.routers.webhook`, not a placeholder |
| `src/app/middlewares/` | Correlation-id middleware |
| `src/app/db/` | SQLAlchemy base, models, session factory and the Redis key layout (S2.2) |
| `src/db/` | Compatibility package for import paths that predate `app.db`; canonical database code is under `src/app/db/` |
| `src/app/auth/` | OAuth: `token_manager.py` acquires and refreshes the ServiceNow access token, including mid-run expiry (S1.5); `webhook_oauth.py` issues and verifies the webhook JWT (#139, #143) |
| `src/app/clients/` | Outbound integrations: `servicenow_client.py` (S1.5 Table API client) and `qdrant.py` (S1.4 vector store) |
| `src/app/models/` | Pydantic domain models: `incident.py`, `execution_log.py`, `oauth.py`, `work_note.py` (S1.5) and `knowledge.py` (S1.4) |
| `src/app/exceptions/` | Typed ServiceNow errors raised by the client (S1.5) |
| `src/app/publishing/` | S1.4 ServiceNow KB publishing: `servicenow_kb.py` (idempotent upsert over OAuth), `payload.py`, `html.py`, `provisioning.py` |
| `src/app/retrieval/` | S1.4 knowledge pipeline: `barq_manual.py`, `extraction.py`, `chunking.py`, `embedding.py`, `ingest.py`, `search.py`, `sources.py` |
| `src/app/utils/` | Shared helpers, including timezone handling for ServiceNow datetimes |
| `src/retrieval/` | Re-export shim onto `src/app/retrieval/`. Kept for import paths predating the `app` package; no logic of its own |
| `src/app/repositories/` | Persistence layer: `audit.py` and `idempotency.py` against PostgreSQL (S2.2) |
| `src/app/services/` | Intentionally empty — business logic lands here |
| `src/app/workers/` | Celery application, producer, replay, retry policy, sync engine and the incident task (S2.3) |
| `src/workers/` | Compatibility re-export of `src/app/workers/`; no logic of its own |
| `src/agent/` | The LangGraph state machine: `graph.py`, `nodes/` (load → validate → classify → determine_risk → retrieve → generate → verify_evidence → safety_check → act), `llm.py` (Gemini through the LiteLLM proxy), `retrieval.py`, `policy.py` and `checkpointer.py` (S2.4, S2.5, S3.4) |
| `src/observability/` | Langfuse tracing and the log/trace redaction helpers |
| `eval/` | Retrieval ablation and the report generator that writes `docs/sprint-2/s2.4-hybrid-retrieval/` (S2.4) |
| `migrations/` | Alembic versions for the PostgreSQL state schema (S2.2) |
| `scripts/` | Operational entry points: `verify_permissions.py` (S1.2 permission harness), `extract_barq_kb.py`, `validate_corpus.py`, `setup_qdrant.py`, `seed_qdrant.py`, `publish_kb.py` (S1.4); `test_client.py` (S1.5 client exercise); `export_openapi.py`, `s2_3_cli_walkthrough.sh` |
| `data/` | `corpus/` — the knowledge articles and ingestion report; `coverage_matrix.csv` — the incident-to-article ground truth (S1.4) |
| `tests/` | pytest suite. `conftest.py` injects fake ServiceNow settings so tests never depend on a local `.env` |
| `servicenow/ai_incident_orchestrator/` | Scoped ServiceNow application: exported update-set XML plus the SDK source it was built from |
| `docs/` | Sprint deliverables, field dictionary, verification records, screenshots |
| `openapi.json` | The generated API contract; `scripts/export_openapi.py [--check]` regenerates it or verifies it is in sync |
| `Dockerfile` | One unified image serving both the FastAPI ingestion API and the Celery workers |
| `docker-compose.yml` | Qdrant, PostgreSQL, Redis, the API and the Celery worker. Ports bind to `127.0.0.1` by default |
| `justfile` | Task shortcuts for the common lint / type-check / test loop |
| `.github/workflows/ci.yml` | Lint, format check, type-check and tests on every PR |
| `.github/workflows/servicenow-sdk.yml` | Builds the scoped app with `--frozenKeys`, runs the S1.3 eligibility tests, and checks the build still reproduces the exported update set (#54, #99) |
| `.github/workflows/codeql.yml` | Weekly and per-PR security scanning of Python and the hand-written ServiceNow JavaScript/TypeScript; generated SDK output is excluded |
| `.github/workflows/labeler.yml`, `.github/labeler.yml` | Auto-labels PRs by which task's paths they touch |
| `TEAM.md` | Who owns which task, and the other project roles |
| `docs/ROADMAP.md` | The full four-sprint PRD scope, not just what is built so far |

`src/app/services/` is intentionally empty. It marks the agreed structure for work that lands later; every other row above matches the tree as it stands.

---

## Getting started

Requires Python 3.12, [`uv`](https://docs.astral.sh/uv/), and Docker for the datastores.

```bash
uv sync                       # install dependencies
cp .env.example .env          # then fill in your own values
docker compose up -d          # Qdrant, PostgreSQL, Redis
just run                      # start the API with reload
```

`POSTGRES_PASSWORD` has no default — Compose refuses to start until you set it. `.env` is gitignored; only `.env.example` is committed, and it must never contain a real credential.

### Quality gate

```bash
just lint        # ruff check
just format      # ruff format
just typecheck   # mypy src
just test        # pytest
just check       # everything CI runs, in the same order
```

CI runs the same commands on every pull request. The tests pass on a clean clone with no `.env` present.

---

## ServiceNow application

Scoped application **AI Incident Orchestrator**, scope `x_2215032_ai_inc_0`. No artifact lives in the Global scope.

The exported update set is at [`servicenow/ai_incident_orchestrator/ai_incident_orchestrator_s1_1.xml`](servicenow/ai_incident_orchestrator/ai_incident_orchestrator_s1_1.xml) — 39 records, every one scoped to the application, zero delete actions. It has been previewed and committed cleanly on a second instance without manual repair.

The reproducible source is the official ServiceNow SDK project under [`servicenow/ai_incident_orchestrator/sdk-app/`](servicenow/ai_incident_orchestrator/sdk-app/). It is not a substitute for the exported update set.

All thirteen Incident columns carry the `x_2215032_ai_inc_0_ai_` prefix. **Scripts must use internal choice values (`in_progress`), never display labels.** Field types, permitted values, writing component and intended write permissions are in the [field dictionary](docs/sprint-1/s1.1-scoped-app-and-field-model/field-model.md).

---

## Sprint 1 — Platform Build

Goal: the platform side exists as a real ServiceNow application, with an audit trail and an identity a risk owner would sign off. Covers FR-01, FR-02 and FR-06. Tracked in the [Sprint 1 milestone](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/milestone/1); Sprints 2–4 are [scoped in the roadmap](docs/ROADMAP.md) with their own milestones for planning, ahead of implementation.

| Task | Scope | Owner |
|---|---|---|
| S1.1 | Scoped application and Incident field model | [@ali-ezz](https://github.com/ali-ezz) |
| S1.2 | AI Execution Log table, OAuth integration identity, field-level ACLs | [@MohamedAbdelaiem](https://github.com/MohamedAbdelaiem) |
| S1.3 | Eligibility Business Rule and identifier-only outbound event | [@ahmedtamer101](https://github.com/ahmedtamer101) |
| S1.4 | Knowledge corpus, ServiceNow KB, Qdrant hybrid collection | [@kerolos-mohsen](https://github.com/kerolos-mohsen) |
| S1.5 | ServiceNow Table API client and incident write-back | [@Tasneemmohammed0](https://github.com/Tasneemmohammed0) |

**Definition of done — the target state, not a claim about today.** The application exports cleanly as an update set; an execution log record can be written through the API by the integration user; and no admin credential exists anywhere in the repository or configuration. Progress against each clause is tracked in the Sprint 1 issues.

### S1.1 documentation

- [Field dictionary](docs/sprint-1/s1.1-scoped-app-and-field-model/field-model.md) — types, values, writers, permissions, and why suggestion and resolution are separate fields
- [Implementation runbook](docs/sprint-1/s1.1-scoped-app-and-field-model/implementation-runbook.md)
- [Acceptance evidence matrix](docs/sprint-1/s1.1-scoped-app-and-field-model/acceptance-matrix.md)
- [Source-PDI verification](docs/sprint-1/s1.1-scoped-app-and-field-model/source-pdi-verification.md)
- [Secondary-PDI clean-import verification](docs/sprint-1/s1.1-scoped-app-and-field-model/secondary-import-verification.md)
- [Submission checklist](docs/sprint-1/s1.1-scoped-app-and-field-model/submission-checklist.md)
- [Screenshot evidence](docs/sprint-1/s1.1-scoped-app-and-field-model/screenshots/)

---

## Contributing

Branch from `main`, open a pull request, and let CI run. Work is tracked in GitHub issues labelled by task (`S1.1` … `S1.5`) against the Sprint milestone.

Two rules worth stating explicitly, because both have bitten this repository:

- **Never commit a real credential**, including in `.env.example`. Admin-credential authentication is an explicit exclusion in the PRD, so `admin` must not appear even as a placeholder.
- **Do not force-push shared branches.** Rewriting history orphans open pull requests, and GitHub cannot reopen them afterwards.
