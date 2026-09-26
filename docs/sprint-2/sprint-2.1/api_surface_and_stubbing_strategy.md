# Sprint 2–4 API Surface & Endpoint Stubbing Strategy

## 1. Executive Summary

This document specifies the architectural rationale and delivery boundaries for the platform's HTTP API surface established during **Sprint 2 (S2.1)**. 

Per the Sprint 2 mandate, the FastAPI application must expose a **complete, schema-validated endpoint surface** across Sprints 2 through 4 so that frontend dashboards, ServiceNow integrations, and external consumers can build against stable contracts without experiencing 404s or application crashes.

---

## 2. Workstream Demarcation: Real Business Logic vs. Contract Stubs

To respect sprint boundaries and teammate workstream ownership, endpoints are categorized into two tiers:

```
+---------------------------------------------------------------------------------------------------+
| TIER 1: ACTIVE SPRINT 2 ENDPOINTS (Full Production Logic)                                         |
+---------------------------------------------------------------------------------------------------+
| • POST /api/v1/webhook/incident : Complete Bearer auth, Contract v1 validation, PostgreSQL        |
|                                   atomic idempotency, Redis queueing, and immediate 202 Accepted. |
| • GET /health, /ready          : Process liveness + readiness probes (PostgreSQL + Redis).       |
| • POST /api/v1/oauth/token      : OAuth client-credentials exchange; issues the webhook or        |
|                                   operator JWT that every Bearer route then requires.           |
| • Execution Endpoints           : Reads live data from the operational database schema            |
|   (GET /executions/{id})          established by Ahmed in S2.2.                                   |
| • HITL Approvals                : Real PostgreSQL reads/writes on the approvals table.            |
|   (GET /approvals, GET /approvals/{id}, POST /approvals/{id}/decide, and                          |
|    GET /approvals/pending/{execution_id} — the last added by S3.4)                                |
| • DLQ Management                : Real Redis list/replay logic with Operator RBAC.               |
|   (GET /dlq, POST /dlq/{event_id}/replay)                                                        |
| • GET /api/v1/config            : Live settings inspection with all secrets redacted.             |
| • Langfuse Tracing              : Initialised on startup; fails gracefully when unreachable.      |
+---------------------------------------------------------------------------------------------------+
                                                  │
                                                  ▼
+---------------------------------------------------------------------------------------------------+
| TIER 2: SPRINTS 3–4 DOWNSTREAM ENDPOINTS (Strict Contract Stubs)                                  |
+---------------------------------------------------------------------------------------------------+
| • Evaluation & Benchmarking (GET /eval/results, POST /eval/run)                                   |
|                                                                                                   |
| Strategy: Implemented with strict Pydantic V2 request & response validation (extra="forbid")      |
| and valid mock/placeholder responses, but WITHOUT deep backend execution logic.                   |
+---------------------------------------------------------------------------------------------------+
```

---

## 3. Detailed Endpoint Strategy

### 3.1. Executions (`src/api/routers/executions.py`)
* **Schemas**: `ExecutionResponse`, `TraceResponse`, `IncidentExecutionsResponse` in [src/api/schemas/executions.py](../../../src/api/schemas/executions.py).
* **Business Logic Status**: **Implemented with Real Database Logic**.
* **Rationale**: Ahmed Tamer (S2.2) already deployed the canonical PostgreSQL operational schema (`events`, `executions`, `workflow_state`). There is no `execution_node_states` *table*: per-node attempts live in `workflow_state`, and the ORM class that maps it is named `ExecutionNodeState`. The full set of seven tables is `events`, `idempotency_keys`, `executions`, `workflow_state`, `approvals`, `failures`, `retry_state`. Queries for incident executions and execution statuses read directly from the database session. If an execution has not yet generated detailed agent trace steps, the trace endpoint returns a structured response indicating trace progression.

### 3.2. HITL Approvals (`src/api/routers/approvals.py`)
* **Schemas**: `ApprovalResponse`, `ApprovalDecisionRequest` in [src/api/schemas/approvals.py](../../../src/api/schemas/approvals.py).
* **Business Logic Status**: **Real Database Logic**.
* **Rationale**: `GET /approvals` and `GET /approvals/{id}` query the live PostgreSQL `approvals` table. `POST /approvals/{id}/decide` writes an approval record for an existing execution and refuses a second one: `409` if that execution is already decided, `404` if the id resolves to neither an approval nor an execution. There is no stub fallback — a decision that was never stored is never reported as stored (#147). The decider is read from the operator token, not from the body (#148). Note that the 404 is real at runtime but is **not declared in the committed `openapi.json`**, which specifies only `200` and `422` for these routes; the behaviour is under-documented, not wrong.

### 3.3. Dead-Letter Queue (DLQ) Management (`src/api/routers/dlq.py`)
* **Schemas**: `DLQEventResponse`, `DLQReplayResponse` in [src/api/schemas/dlq.py](../../../src/api/schemas/dlq.py).
* **Business Logic Status**: **Real Redis Logic + RBAC Enforcement**.
* **Rationale**: `GET /dlq` reads all events from the `barq:incident:dlq` Redis key via `LRANGE`. `POST /dlq/{event_id}/replay` pops the matching event from the DLQ and re-pushes it to `barq:incident:events` for reprocessing. Both endpoints enforce strict **Operator Role RBAC** — the `operator` role must be present in the verified token's `roles` claim, returning `403 PERMISSION_DENIED` when it is not (#148).

### 3.4. Evaluation & Benchmarking (`src/api/routers/eval.py`)
* **Schemas**: `EvalRunRequest`, `EvalRunResponse`, `EvalResultResponse` in [src/api/schemas/eval.py](../../../src/api/schemas/eval.py).
* **Business Logic Status**: **Contract Stub**.
* **Rationale**: The benchmark evaluation runner against test incident datasets is scheduled for **Sprint 4**. The endpoints validate dataset parameters and return structured execution IDs and placeholder metric envelopes.

### 3.5. Runtime Configuration (`src/api/routers/config.py`)
* **Schemas**: `RedactedConfigResponse` in [src/api/schemas/config.py](../../../src/api/schemas/config.py).
* **Business Logic Status**: **Real Inspection with Secret Hygiene**.
* **Rationale**: Returns application runtime metadata (environment, version, hostnames) while strictly redacting sensitive credentials (passwords, tokens, client secrets to `"***REDACTED***"`).

---

## 4. Key Architectural Guarantees

1. **Strict Request Validation (`extra="forbid"`)**: Any unexpected fields or invalid types submitted to these endpoints will be rejected with HTTP 422 `CONTRACT_VALIDATION_FAILED`.
2. **Deterministic Response Contracts**: All responses strictly match their Pydantic V2 definitions, ensuring complete compatibility with the committed [openapi.json](../../../openapi.json).
3. **Zero Downstream Execution on Request Thread**: Neither the active webhook nor the endpoint stubs invoke heavy model execution, vector search, or synchronous ServiceNow calls during the request cycle.
4. **Seamless Upgrades in Sprints 3 & 4**: When background workers (Sprint 3) and benchmark runners (Sprint 4) are completed, the endpoint logic can be swapped in internally without altering the public API schema.
