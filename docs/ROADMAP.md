# Roadmap

The full PRD scope across four one-week sprints. Text below is taken directly from the
PRD; nothing here is inferred beyond the due dates, which are marked as provisional.

Requirement IDs (`FR-*`, `A-*`, `NFR-*`) refer to the PRD's Functional Requirements,
List of Features and Non-Functional Requirements sections.

## Sprint 1 — Platform Build

**Goal:** the platform side exists as a real ServiceNow application, with an audit
trail and an identity a risk owner would sign off.

Requirements: FR-01, FR-02, FR-06 · A-01, A-02, A-05

Definition of done: the application exports cleanly as an update set; an execution log
record can be written through the API by the integration user; no admin credential
exists anywhere in the repository or configuration.

Tracked in the [Sprint 1 milestone](../../milestone/1). See [TEAM.md](../TEAM.md) for
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

## Sprint 3 — Retrieval & Reasoning

**Goal:** an explicit, checkpointed state machine that retrieves well and knows when
not to act.

Scope: ingestion producing dense and sparse vectors in one Qdrant collection with full
metadata including security level; hybrid retrieval with fusion, metadata filtering and
reranking; dense-only baseline kept switchable so the hybrid improvement is measured,
not asserted; the LangGraph state machine as explicit nodes with explicit edge
conditions; checkpointing so an interrupted execution resumes from its last completed
node; risk determined before retrieval, with high-risk incidents routed to the human
path; the PostgreSQL schema completed.

Requirements: FR-11 → FR-15 · A-08, A-09, A-10, A-11

Definition of done: a live incident runs the full graph to a cited draft; a high-risk
incident stops at the risk node; killing the process mid-run and retrying resumes from
the checkpoint rather than restarting.

## Sprint 4 — Trust & Hardening

**Goal:** make it operable — permission classes, human approval, guardrails, evaluation
in CI, and a demo that includes deliberate failure.

Scope: tool registry with permission classes (read, low-risk write, high-risk), only
registered tools callable; LangGraph interrupt and resume for high-risk actions and
below-threshold confidence; input guardrails for prompt-injection screening and
credential/PII redaction; output guardrails for schema validation, per-step evidence
verification and a server-enforced tool allowlist; an adversarial red-team set with
recorded results; Langfuse tracing with cost, latency and prompt-version linkage; a
versioned evaluation dataset wired into CI as a regression gate; a reliability pass
covering timeouts, retries, dead-letter handling, fallback paths and health checks;
GitHub Actions building a deployable image.

Requirements: FR-16 → FR-20 and all ten NFRs · A-12, A-13, A-14, A-15

Definition of done: no high-risk action reaches ServiceNow without a recorded approval;
a deliberately regressed prompt fails the CI evaluation gate; a worker killed
mid-execution recovers without duplicating a write.

## Demo Day

Live run: an automated resolution, a human approval, a guardrail block, and a
worker-crash recovery. Evidence: evaluation report with baseline vs hybrid vs
reranked, trace set, red-team results, operations runbook. Handover: update set, event
contract, PostgreSQL migrations, CI pipeline, Compose stack, architecture pack.
