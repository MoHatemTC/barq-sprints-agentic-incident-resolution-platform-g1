# Sprint 2 (S2.1) — API Surface, Schemas & Error Taxonomy Specification

**Document Version**: 1.0.0  
**Application Title**: BARQ Agentic Incident Resolution Platform API  
**Base Path**: `/`  
**OpenAPI Specification**: Committed at [openapi.json](../../openapi.json)

---

## 1. Authentication & Security Architecture

The platform enforces multi-tiered security across all exposed HTTP interfaces:

### A. Bearer Token Authentication
All `/api/v1/*` routes require a Bearer token in the `Authorization` header:
```http
Authorization: Bearer <WEBHOOK_AUTH_TOKEN>
```
* Validation is performed using constant-time digest comparison (`secrets.compare_digest`) in [app/auth/auth.py](../../src/app/auth/auth.py) to prevent timing attacks.
* Missing or invalid tokens return `HTTP 401 Unauthorized`.

### B. Role-Based Access Control (RBAC)
Sensitive administrative endpoints (such as DLQ replay) require the Operator role:
```http
X-User-Role: operator
```
* Non-operator callers attempting operator-restricted operations receive `HTTP 403 Forbidden`.

### C. Correlation ID Propagation (EC-09)
* Every incoming request receives or generates a unique correlation ID via `CorrelationIdMiddleware`.
* Extracted from `X-Correlation-ID` or generated as a UUID v4.
* Bound to thread-local `structlog` context variables and returned in response headers and error envelopes.

---

## 2. Comprehensive Route Table (Sprints 2–4)

| HTTP Method | Route Path | Auth / Role | Status Code | Purpose / Lifecycle Stage |
|---|---|:---:|:---:|---|
| **GET** | `/health` | None | `200 OK` | Process liveness probe |
| **GET** | `/ready` | None | `200 OK` / `503` | Dependency readiness probe (PostgreSQL + Redis) |
| **POST** | `/api/v1/webhook/incident` | Bearer | `202 Accepted` | Ingest inbound ServiceNow incident event |
| **GET** | `/api/v1/executions/{execution_id}` | Bearer | `200 OK` / `404` | Retrieve execution status and metadata |
| **GET** | `/api/v1/executions/{execution_id}/trace` | Bearer | `200 OK` / `404` | Retrieve workflow node execution trace |
| **GET** | `/api/v1/incidents/{sys_id}/executions` | Bearer | `200 OK` | List all executions for an incident sys_id |
| **GET** | `/api/v1/approvals` | Bearer | `200 OK` | List recorded human approval decisions |
| **GET** | `/api/v1/approvals/{id}` | Bearer | `200 OK` / `404` | Get specific approval decision details |
| **POST** | `/api/v1/approvals/{id}/decide` | Bearer | `200 OK` | Submit human operator approval decision |
| **GET** | `/api/v1/dlq` | Bearer | `200 OK` | List all dead-lettered events |
| **POST** | `/api/v1/dlq/{event_id}/replay` | Bearer + Operator | `202 Accepted` | Replay a dead-lettered event into active queue |
| **GET** | `/api/v1/config` | Bearer | `200 OK` | Inspect runtime configuration (secrets redacted) |
| **POST** | `/api/v1/eval/run` | Bearer | `202 Accepted` | Trigger diagnostic model evaluation benchmark |
| **GET** | `/api/v1/eval/results` | Bearer | `200 OK` | Retrieve evaluation benchmark results |

---

## 3. Schema Definitions

### A. Ingestion Webhook

#### Request (`IncidentWebhookPayload`)
```json
{
  "event_id": "993a4b5c-6d7e-8f90-a1b2-c3d4e5f60718",
  "sys_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
  "number": "INC0042567",
  "event_type": "incident.created",
  "contract_version": "v1"
}
```

#### Response (`WebhookAcceptedResponse`, HTTP 202)
```json
{
  "status": "accepted",
  "event_id": "993a4b5c-6d7e-8f90-a1b2-c3d4e5f60718",
  "correlation_id": "c7943a85-a835-423c-8231-282f4663f77a",
  "idempotent_replay": false
}
```

---

### B. Execution Audit & Tracing

#### Execution Response (`ExecutionResponse`, HTTP 200)
```json
{
  "execution_id": "11111111-1111-1111-1111-111111111111",
  "incident_sys_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
  "status": "succeeded",
  "node_reached": "remediate",
  "model_name": "gemini-2.5-pro",
  "agent_version": "v1.0.0",
  "started_at": "2026-09-18T07:15:00Z",
  "ended_at": "2026-09-18T07:15:12Z",
  "termination_cause": "Remediation plan applied and verified"
}
```

#### Trace Response (`TraceResponse`, HTTP 200)
```json
{
  "execution_id": "11111111-1111-1111-1111-111111111111",
  "incident_sys_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
  "status": "succeeded",
  "node_states": [
    {
      "sequence_number": 1,
      "node_name": "triage",
      "attempt": 1,
      "status": "succeeded",
      "started_at": "2026-09-18T07:15:00Z",
      "ended_at": "2026-09-18T07:15:02Z",
      "evidence": [{"check": "severity", "result": "SEV-2"}],
      "decision": {"action": "proceed_to_diagnosis"}
    }
  ]
}
```

---

### C. Dead-Letter Queue (DLQ)

#### DLQ Item Response (`DLQEventResponse`, HTTP 200)
```json
{
  "event_id": "evt-dlq-001",
  "payload": {
    "sys_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
    "number": "INC0042567"
  },
  "failure_reason": "ServiceNow API timeout after 3 retries",
  "retry_count": 3,
  "failed_at": "2026-09-18T07:10:00Z"
}
```

#### DLQ Replay Response (`DLQReplayResponse`, HTTP 202)
```json
{
  "event_id": "evt-dlq-001",
  "status": "accepted",
  "correlation_id": "corr-replay-999",
  "message": "Event 'evt-dlq-001' replayed from DLQ into active queue."
}
```

---

## 4. Global Error Taxonomy

All exceptions return a uniform, structured JSON error envelope across the entire API surface:

```json
{
  "error": {
    "code": "AUTHENTICATION_FAILED",
    "message": "Missing or invalid Bearer token",
    "correlation_id": "c7943a85-a835-423c-8231-282f4663f77a",
    "details": {}
  }
}
```

### Standardized Error Codes

| HTTP Status | Error Code | Trigger Condition |
|---|---|---|
| **400 Bad Request** | `INVALID_REQUEST` | Malformed URL parameters or malformed JSON syntax |
| **401 Unauthorized** | `AUTHENTICATION_FAILED` | Missing, expired, or invalid Bearer token |
| **403 Forbidden** | `PERMISSION_DENIED` | Missing required `X-User-Role` (e.g. non-operator calling DLQ replay) |
| **404 Not Found** | `RESOURCE_NOT_FOUND` | Execution, Approval, or Incident sys_id does not exist |
| **409 Conflict** | `CONFLICT` | Concurrent state transition conflict |
| **422 Unprocessable** | `VALIDATION_ERROR` | Pydantic schema validation failure or invalid `contract_version` |
| **500 Server Error** | `INTERNAL_SERVER_ERROR` | Unhandled internal runtime exception (trace logged, secrets masked) |
| **503 Unavailable** | `SERVICE_UNAVAILABLE` | PostgreSQL or Redis connection failure |
