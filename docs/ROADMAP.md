# Roadmap

The PRD scope across four one-week sprints, updated where the team moved work into
earlier sprints. The task briefs in Airtable and the code merged on GitHub determine
delivery status; this roadmap records scope rather than grading.

Requirement IDs (`FR-*`, `A-*`, `NFR-*`) refer to the PRD's Functional Requirements,
List of Features and Non-Functional Requirements sections.

## Sprint 1 — Platform Build

**Goal:** the platform side exists as a real ServiceNow application, with an audit
trail and an identity a risk owner would sign off.

Requirements: FR-01, FR-02, FR-06 · A-01, A-02, A-05

Definition of done: the application exports cleanly as an update set; an execution log
record can be written through the API by the integration user; no admin credential
exists anywhere in the repository or configuration.

Tracked in the [Sprint 1 milestone](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/milestone/1). See [TEAM.md](../TEAM.md) for
task ownership.

## Sprint 2 — Event Integration

**Goal:** ServiceNow talks to the backend, nothing polls, and the ingestion path
survives bursts.

Scope: Business Rule on insert and relevant update with the full eligibility set;
RESTMessageV2 outbound call carrying event ID, sys_id, number and event type only;
FastAPI webhook with authentication, Pydantic validation, 401/422 rejection, 202
Accepted after enqueueing; idempotency keys persisted in PostgreSQL; Redis queue and
Celery workers with exponential backoff and a dead-letter path; first cut of the
PostgreSQL schema; Execution Log written on every attempt including failures.

Requirements: FR-03, FR-04, FR-05, FR-07 → FR-10, FR-15 (first cut) · A-03, A-04, A-06,
A-07, A-11

Definition of done: creating an eligible incident produces a 202 within 500ms at p95;
the same event fired twice produces exactly one execution; a job failing repeatedly
lands in the dead-letter path rather than looping.

## Sprint 3 — Multi-Agent Resolution & Human Control

> Ingestion, hybrid retrieval and the LangGraph state machine were moved forward
> into Sprint 2 as S2.4 and S2.5 and shipped there, so they are no longer Sprint 3
> scope. Sprint 3 is the reasoning and control layer built on top of them. The
> authoritative task table, owners and merge state is in [README.md](README.md).

**Goal:** the graph reasons with more than one agent, cannot act without authority, and
stops for a person before anything reaches ServiceNow.

Scope: multi-agent diagnosis and resolution with a critic that verifies every step against
retrieved evidence; a tool registry where every tool declares a permission class and only
registered tools are callable; input and output guardrails; a true LangGraph
interrupt/resume with an approval audit trail and crash recovery at the write boundary;
and capture of a human resolution back into the knowledge base and the vector store.

Requirements: FR-16 → FR-18, plus FR-12 · A-12, A-13, A-14

Status checked on 2026-09-28: Airtable marks all five Sprint 3 tasks `Done`.
All five have merged code on `main`, including S3.4 in
[PR #158](https://github.com/MoHatemTC/barq-sprints-agentic-incident-resolution-platform-g1/pull/158).
S3.3 includes input screening, redaction and a real `safety_check`; it is no
longer a pass-through. The merged code and Airtable status do not by themselves
prove every shared demo path has passed live verification.

Definition of done: a live incident runs the full graph to a cited draft; a high-risk
incident stops at the risk node; killing the process mid-run and retrying resumes from
the checkpoint rather than restarting.

## Sprint 4 — Trust & Hardening

**Goal:** make it operable — permission classes, human approval, guardrails, evaluation
in CI, and a demo that includes deliberate failure.

Scope: input guardrails for prompt-injection screening and credential/PII redaction;
output guardrails for schema validation, per-step evidence verification and a
server-enforced tool allowlist; an adversarial red-team set with recorded results; a
versioned evaluation dataset wired into CI as a regression gate; a reliability pass
covering timeouts, retries, dead-letter handling, fallback paths and health checks;
GitHub Actions building a deployable image on every pull request.

Implemented ahead of Sprint 4: the tool registry with permission classes and
server-side allowlist (S3.2), LangGraph interrupt and resume for high-risk actions and
below-threshold confidence (S3.4, merged in #158), input and output guardrails
(S3.3), and Langfuse tracing with per-node spans, retrieval, tool calls, generations,
token usage, latency and cost (S2.5, S3.1).

### Sprint 4 status (checked 2026-10-02)

Airtable now holds the Sprint 4 tasks, due 2026-10-03: S4.1 (HITL pause/resume fix and a
chatbot-usable ServiceNow tool), S4.2 (semantic caching), S4.3 (DeepEval/RAGAS evaluation
suite) and S4.4 (ServiceNow chatbot, frontend and backend; listed twice, once for each of its two
owners). Airtable is authoritative for status; this is what is on `main`.

Built: semantic caching and single-flight clustering (S4.2, #204/#208/#210, with the
follower governance gate added in the audit); the HITL fixes (#182–#184, #191); the
evaluation harness and dataset (#190, #209); query rewriting and article-level diversity
(#191); residual-PII detection, default off (#189); and a 78-case red-team corpus with
recorded results ([audit](audit_2026-10-02.md)).

**Not built against the PRD's Sprint 4 definition of done:**

| PRD requirement | State |
|---|---|
| Evaluation gate in CI that fails the build on a regression (FR-20; "a deliberately regressed prompt fails the CI evaluation gate") | Not built. `ci.yml` still says so; `/api/v1/eval/run` returns 501 |
| GitHub Actions builds a deployable image on every pull request (NFR-10) | Not built; images are built on the deployment host |
| Operations runbook, architecture and data-flow pack | [Operations runbook](operations_runbook.md) now exists; the data-flow diagram is the graph diagram only |
| NFR-02 end-to-end p95 ≤ 90 s measured from traces | Indicative only (p95 72.6 s, n = 9, shared system) |
| Chatbot tool (S4.1 part 2) and chatbot (S4.4) | Not built |

Where an item here conflicts with the PRD text, the PRD scope is the contract and this
table is the gap.
The remaining PRD scope above has no assigned Sprint 4 task owner or deadline yet.
The repository has an adversarial seed set and a retrieval evaluation set, but the
PRD's evaluation regression gate and deployable-image build on every pull request
are not in `.github/workflows/ci.yml` yet. They remain Sprint 4 delivery work.

Requirements: FR-18 → FR-20 and the remaining NFRs · A-14, A-15

Definition of done: no high-risk action reaches ServiceNow without a recorded approval;
a deliberately regressed prompt fails the CI evaluation gate; a worker killed
mid-execution recovers without duplicating a write.

## Demo Day

Live run: an automated resolution, a human approval, a guardrail block, and a
worker-crash recovery. Evidence: evaluation report with baseline vs hybrid vs
reranked, trace set, red-team results, operations runbook. Handover: update set, event
contract, PostgreSQL migrations, CI pipeline, Compose stack, architecture pack.
