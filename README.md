# AI Incident Orchestrator

[![CI](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)
[![Sprint 1](https://img.shields.io/github/milestones/progress/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/1)](../../milestone/1)

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
| `src/app/api/` | HTTP routes. `webhook.py` is a placeholder — Sprint 2 |
| `src/app/auth/` | OAuth token handling. Placeholder — lands with the ServiceNow client |
| `src/app/clients/` | Outbound integrations, starting with the ServiceNow Table API client |
| `src/app/models/` | Pydantic request, response and domain models |
| `src/app/repositories/` | Persistence layer — Sprint 2 |
| `src/app/services/` | Business logic and orchestration |
| `src/app/core/constants.py`, `core/logging.py` | Placeholders, not yet imported anywhere |
| `tests/` | pytest suite. `conftest.py` injects fake ServiceNow settings so tests never depend on a local `.env` |
| `servicenow/ai_incident_orchestrator/` | Scoped ServiceNow application: exported update-set XML plus the SDK source it was built from |
| `docs/` | Sprint deliverables, field dictionary, verification records, screenshots |
| `docker-compose.yml` | Qdrant, PostgreSQL and Redis. Ports bind to `127.0.0.1` by default |
| `.github/workflows/ci.yml` | Lint, format check, type-check and tests on every PR |
| `.github/workflows/codeql.yml` | Weekly and per-PR security scanning of `src/`, `scripts/`, `tests/` |
| `.github/workflows/labeler.yml`, `.github/labeler.yml` | Auto-labels PRs by which task's paths they touch |
| `TEAM.md` | Who owns which task, and the other project roles |
| `docs/ROADMAP.md` | The full four-sprint PRD scope, not just what is built so far |

Several `src/app` modules are intentionally empty. They mark the agreed structure for work that lands in later sprints; the table above says which.

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

All thirteen Incident columns carry the `x_2215032_ai_inc_0_ai_` prefix. **Scripts must use internal choice values (`in_progress`), never display labels.** Field types, permitted values, writing component and intended write permissions are in the [field dictionary](docs/sprint1_field_model.md).

---

## Sprint 1 — Platform Build

Goal: the platform side exists as a real ServiceNow application, with an audit trail and an identity a risk owner would sign off. Covers FR-01, FR-02 and FR-06. Tracked in the [Sprint 1 milestone](../../milestone/1); Sprints 2–4 are [scoped in the roadmap](docs/ROADMAP.md) with their own milestones for planning, ahead of implementation.

| Task | Scope | Owner |
|---|---|---|
| S1.1 | Scoped application and Incident field model | [@ali-ezz](https://github.com/ali-ezz) |
| S1.2 | AI Execution Log table, OAuth integration identity, field-level ACLs | [@MohamedAbdelaiem](https://github.com/MohamedAbdelaiem) |
| S1.3 | Eligibility Business Rule and identifier-only outbound event | [@ahmedtamer101](https://github.com/ahmedtamer101) |
| S1.4 | Knowledge corpus, ServiceNow KB, Qdrant hybrid collection | [@kerolos-mohsen](https://github.com/kerolos-mohsen) |
| S1.5 | ServiceNow Table API client and incident write-back | [@Tasneemmohammed0](https://github.com/Tasneemmohammed0) |

**Definition of done — the target state, not a claim about today.** The application exports cleanly as an update set; an execution log record can be written through the API by the integration user; and no admin credential exists anywhere in the repository or configuration. Progress against each clause is tracked in the Sprint 1 issues.

### S1.1 documentation

- [Field dictionary](docs/sprint1_field_model.md) — types, values, writers, permissions, and why suggestion and resolution are separate fields
- [Implementation runbook](docs/sprint1_implementation_runbook.md)
- [Acceptance evidence matrix](docs/sprint1_acceptance_matrix.md)
- [Source-PDI verification](docs/sprint1_source_pdi_verification.md)
- [Secondary-PDI clean-import verification](docs/sprint1_secondary_import_verification.md)
- [Submission checklist](docs/sprint1_submission_checklist.md)
- [Screenshot evidence](docs/screenshots/s1-1/)

---

## Contributing

Branch from `main`, open a pull request, and let CI run. Work is tracked in GitHub issues labelled by task (`S1.1` … `S1.5`) against the Sprint milestone.

Two rules worth stating explicitly, because both have bitten this repository:

- **Never commit a real credential**, including in `.env.example`. Admin-credential authentication is an explicit exclusion in the PRD, so `admin` must not appear even as a placeholder.
- **Do not force-push shared branches.** Rewriting history orphans open pull requests, and GitHub cannot reopen them afterwards.
