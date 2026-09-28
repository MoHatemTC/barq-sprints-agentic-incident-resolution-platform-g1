# Team

BARQ × Sprints, G1 workstream. No personal contact details here on purpose — this is a
public repository. Reach people through GitHub, or through the programme's Circle
community for anything that shouldn't be public.

## Workstream owners

Each owner is accountable for their task end to end: implementation, verification, and
keeping their issue updated. Ownership below is per sprint, Sprints 1 to 3; Sprints 4 and
beyond are not yet assigned. Scope wording is transcribed from the sprint tables in the
[root README](README.md), which is the source of truth for who owns what.

### Sprint 1 — Platform Build

The [Sprint 1 field model](docs/sprint-1/s1.1-scoped-app-and-field-model/field-model.md) is
the contract every workstream writes against — anyone changing it should expect it to
ripple into the other four.

| Task | Owner | Scope | Tracking |
|---|---|---|---|
| **S1.1** | [@ali-ezz](https://github.com/ali-ezz) | Scoped application and Incident field model | Merged — [PR #1](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/1) |
| **S1.2** | [@MohamedAbdelaiem](https://github.com/MohamedAbdelaiem) | AI Execution Log table, OAuth integration identity, field-level ACLs | [#7](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/7), [#8](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/8) |
| **S1.3** | [@ahmedtamer101](https://github.com/ahmedtamer101) | Eligibility Business Rule, identifier-only outbound event | [#10](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/10) |
| **S1.4** | [@kerolos-mohsen](https://github.com/kerolos-mohsen) | Knowledge corpus, ServiceNow KB, Qdrant hybrid collection | [#11](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/11) |
| **S1.5** | [@Tasneemmohammed0](https://github.com/Tasneemmohammed0) | ServiceNow Table API client, incident write-back | [#6](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/6), [#9](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/9), [#13](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/issues/13) |

### Sprint 2 — Event Integration

| Task | Owner | Scope | Tracking |
|---|---|---|---|
| **S2.1** | [@MohamedAbdelaiem](https://github.com/MohamedAbdelaiem) | FastAPI application: webhook, all project endpoints and 202 semantics | Merged — [#130](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/130) |
| **S2.2** | [@ahmedtamer101](https://github.com/ahmedtamer101) | PostgreSQL state schema, migrations and idempotency enforcement | Merged — [#128](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/128) |
| **S2.3** | [@kerolos-mohsen](https://github.com/kerolos-mohsen) | Redis queue, Celery workers, exponential backoff and a dead-letter path | Merged — [#127](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/127) |
| **S2.4** | [@Tasneemmohammed0](https://github.com/Tasneemmohammed0) | Hybrid retrieval: fusion, metadata filtering, reranking and a measured baseline | Merged — [#110](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/110) |
| **S2.5** | [@ali-ezz](https://github.com/ali-ezz) | Langfuse tracing, agent initialisation and the explicit LangGraph state machine | Merged — [#129](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/129) |
| **S2.6** | [@Tasneemmohammed0](https://github.com/Tasneemmohammed0) | RAG corpus hardening: OCR images, nested/merged-cell tables, multi-column layouts | Merged — [#155](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/155) |

### Sprint 3 — Retrieval & Reasoning

| Task | Owner | Scope | State |
|---|---|---|---|
| **S3.1** | [@MohamedAbdelaiem](https://github.com/MohamedAbdelaiem) | Multi-agent diagnosis and resolution: diagnostic, resolution and critic/verifier agents | Merged — [#156](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/156) |
| **S3.2** | [@ahmedtamer101](https://github.com/ahmedtamer101) | Tool registry, permission classes and server-side allowlist enforcement | Merged — [#157](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/157) |
| **S3.3** | [@Tasneemmohammed0](https://github.com/Tasneemmohammed0) | Input and output guardrails: injection screening, redaction, schema validation, enforcing `safety_check` | In progress |
| **S3.4** | [@ali-ezz](https://github.com/ali-ezz) | True LangGraph interrupt/resume, approval audit trail and crash-recovery completion | In review — [#158](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/158) |
| **S3.5** | [@kerolos-mohsen](https://github.com/kerolos-mohsen) | Human-resolution knowledge capture: KB write-back and Qdrant re-ingestion loop | Merged — [#159](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/159) |

## Other roles

| Role | Who | Responsibility |
|---|---|---|
| Scrum Master | [@ali-ezz](https://github.com/ali-ezz) | Sprint rhythm, unblocking, surfacing risk early rather than at the deadline |
| PR reviewer | [@AyaAshraf3](https://github.com/AyaAshraf3) | Human review pass on pull requests |
| Repository admin | [@MoHatemTC](https://github.com/MoHatemTC) | Repository settings, branch protection, anything requiring owner access |
| Mentor | Sarah Nader | Deliverables, technical questions, draft reviews |
| Scope & deadlines | Ahmed Mansour | Milestone accountability, deadline governance |

## Sprint rhythm

Sunday planning · daily check-ins Monday–Wednesday · Thursday retro. If something is
slipping, say so at a check-in — cutting scope mid-week beats hiding it until the
deadline.

## Live status

The authoritative day-to-day status is the [milestones](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/milestones) (Sprint 1 … Sprint 4)
and their issues, not this file. This file answers "who owns what" for Sprints 1 to 3 and
changes rarely; the milestone answers "what's done right now" and changes daily. The
Tracking and State columns above are a snapshot copied from the sprint tables in the
[root README](README.md), not a live status feed.
