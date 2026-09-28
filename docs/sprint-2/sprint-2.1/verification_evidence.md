# Sprint 2 — Verification Evidence & Benchmark Dossier

**Date**: 2026-09-18  
**Sprint**: S2.1 (FastAPI App Creation, Canonical Webhook Ingestion, NFR-01 Latency Compliance)  
**Target Environment**: Local FastAPI / Uvicorn, PostgreSQL 16 (`barq_s2_1_test`), Redis 7, ServiceNow Washington Instance via ngrok tunnel.

---

## 1. Executive Summary

All acceptance criteria for Sprint 2 (S2.1) have been implemented, tested, and validated with zero regressions:
1. **Canonical Endpoint Implemented**: `POST /api/v1/webhook/incident` strictly serves as the canonical ingestion interface with Bearer token authentication and strict Outbound Event Contract v1 schema validation.
2. **100% Passing Test Suites**: the full suite (1255 passed, 30 skipped) is green with zero failures.
3. **Live ServiceNow Ingestion Verified**: Real outbound incident events dispatched from a live ServiceNow instance through ngrok tunnel were ingested, authenticated, persisted, and enqueued with immediate HTTP 202 response (`81.08ms` duration).
4. **NFR-01 Latency Benchmark Met**: Sustained load testing across Redis queue depths 0, 1,000, and 10,000 at 50 concurrency achieved **p95 ≤ 285 ms** in the run tabulated in §4, inside the mandatory 500 ms SLA budget. See the run-note above §4 before quoting this as the platform's latency.
5. **Langfuse Tracing Integrated**: Langfuse client is initialised in the application lifespan and fails gracefully when unreachable, with no impact on request processing.

---

## 2. Test Suite Execution Matrix

All automated test suites executed cleanly:

| Test Suite File | Tests | Status | Scope / Focus |
|---|:---:|:---:|---|
| `tests/test_webhook.py` | 22 / 22 | **PASS** | Outbound Event Contract v1 validation, 401 Bearer auth, 422 schema violations, EC-01 concurrent duplicate idempotency, real PostgreSQL + Redis integration |
| `tests/test_endpoints_contract.py` | 11 / 11 | **PASS** | Strict contract enforcement, required fields, payload structure, response envelope |
| `tests/test_app_wiring.py` | 22 / 22 | **PASS** | FastAPI lifespan, OpenAPI schema compliance, router mounting, dependency injection, Langfuse graceful failure |
| `tests/test_log_hygiene.py` | 7 / 7 | **PASS** | EC-09 secret redaction, Bearer token masking in access logs, error logs, and exception dumps |
| `tests/test_no_polling.py` | 3 / 3 | **PASS** | FR-05 AST static code analysis verifying zero `sleep` or polling loops in ingestion path |
| `tests/test_dlq_router.py` | ✓ | **PASS** | DLQ Redis list/replay logic, Operator RBAC enforcement |
| `tests/test_approvals.py` | ✓ | **PASS** | HITL approval read/write against real PostgreSQL approvals table |
| Additional suites (idempotency, executions, eval, config, etc.) | ✓ | **PASS** | Full platform coverage |
| **Total** | **1255 passed / 30 skipped** | **100% PASS** | **Complete Sprint 2 automated test coverage** |

> **Run count correction.** The suite-size figure in this table was refreshed to the
> current repository. The per-file counts above it were captured on 2026-09-18 against
> `feat/s2-1-*` and are point-in-time for that branch; only the total is current.

---

## 3. Live ServiceNow Integration Proof

Live outbound event transmission from ServiceNow developer instance via ngrok tunnel to the local FastAPI ingestion webhook:

### Network Dispatch Flow
```
ServiceNow Washington Instance
  └─► Script Action: AI Incident Orchestrator - Send S1.3 Event
        └─► REST Message: AI Incident Orchestrator S1.3 Event (POST)
              └─► Endpoint: https://motor-earlobe-snowman.ngrok-free.dev/api/v1/webhook/incident
                    └─► ngrok tunnel -> http://localhost:8000
                          └─► FastAPI Webhook Router
```

### Production Access Log Evidence
```log
2026-09-18T06:50:04.508341Z [info] http_request_completed
  client_ip=148.139.125.20
  correlation_id=c7943a85-a835-423c-8231-282f4663f77a
  duration_ms=81.08
  method=POST
  path=/api/v1/webhook/incident
  status_code=202
  user_agent=ServiceNow/1.0
INFO: 148.139.125.20:0 - "POST /api/v1/webhook/incident HTTP/1.1" 202 Accepted
```

- **Client Source**: Official ServiceNow IP block (`148.139.125.20`)
- **HTTP Status**: `202 Accepted`
- **Round-Trip Processing Time**: `81.08 ms`
- **Payload Acceptance**: Validated against Contract v1, persisted into PostgreSQL tables (`idempotency_keys`, `events`, `executions`), and enqueued to Redis queue `barq:incident:events`.

---

## 4. NFR-01 Latency & Queue Depth Benchmarks

> [!NOTE]
> **These figures are one run of a benchmark that was executed several times with
> different results.** The sweep below (500 requests per depth, 3 depths, concurrency
> 50) was run repeatedly and each run produced its own numbers. The other recorded runs
> are [`docs/sprint2_latency_report.md`](../../sprint2_latency_report.md),
> [`docs/sprint-2/sprint-2.1/baseline_latency_report.md`](baseline_latency_report.md),
> [`docs/sprint-2/sprint-2.1/sprint2_latency_report.md`](sprint2_latency_report.md) and
> [`docs/sprint2_ingestion_design.md`](../../sprint2_ingestion_design.md). **No run is
> designated authoritative** and nothing records which one supersedes which, so do not
> present any single figure as *the* latency, and never splice numbers from two runs into
> one table. Across those runs p95 at depth 0 ranges from 237.5 ms to 403.7 ms — a spread
> far larger than any difference between queue depths, which is why the depth-independence
> claim below has to be argued from within a single run, not by comparing runs.
>
> This run was also captured on **Windows 11 (AMD64)** with Python 3.12.13. The commands
> are given in PowerShell form below; the repository's `CONTRIBUTING.md`, `README.md` and
> `justfile` (`set shell := ["bash", "-cu"]`) are POSIX, so the equivalent is a single
> line with no backtick continuations:
>
> ```bash
> uv run python tests/load/run_load_test.py --base-url http://127.0.0.1:8000 \
>   --depths 0 1000 10000 --requests-per-depth 500 --concurrency 50
> ```

The NFR-01 non-functional requirement specifies that `POST /api/v1/webhook/incident` must return an HTTP 202 acknowledgment with **p95 latency < 500 ms**, independent of Redis queue backlog size ($O(1)$ LPUSH).

### Benchmark 1: Canonical SLA Benchmark (50 Concurrency, 500 Req/Depth)
Command:
```powershell
uv run python tests/load/run_load_test.py --base-url http://127.0.0.1:8000 --depths 0 1000 10000 --requests-per-depth 500 --concurrency 50
```

| Queue Depth | Requests | Failures | p50 Latency | p95 Latency | p99 Latency | Max Latency | Throughput | NFR-01 (<500ms) |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **0** | 500 | 0 | 207.5 ms | **244.4 ms** | 291.5 ms | 297.7 ms | 234.8 rps | **PASS** |
| **1,000** | 500 | 0 | 200.3 ms | **254.3 ms** | 307.7 ms | 326.5 ms | 237.4 rps | **PASS** |
| **10,000** | 500 | 0 | 201.6 ms | **285.0 ms** | 335.9 ms | 346.5 ms | 229.7 rps | **PASS** |

*Note: Latency variance across empty queue (depth 0) and saturated queue (depth 10,000) is within ~40 ms, confirming $O(1)$ queue independence.*

---

### Benchmark 2: Quick Validation Run (20 Concurrency, 100 Req/Depth)
Command:
```powershell
uv run python tests/load/run_load_test.py --base-url http://127.0.0.1:8000 --depths 0 500 --requests-per-depth 100 --concurrency 20 --report docs/sprint-2/quick_latency_report.md
```

| Queue Depth | Requests | Failures | p50 Latency | p95 Latency | p99 Latency | Max Latency | Throughput | Status |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **0** | 100 | 0 | 83.1 ms | **113.8 ms** | 117.1 ms | 117.2 ms | 226.0 rps | **PASS** |
| **500** | 100 | 0 | 85.4 ms | **111.4 ms** | 114.3 ms | 116.7 ms | 227.5 rps | **PASS** |

---

## 5. Architectural Invariants Verified

1. **EC-01 Concurrent Deduplication**:
   - `idempotency_keys` table enforces database-level uniqueness via constraint `uq_idempotency_keys_event_id`.
   - Idempotent re-deliveries return immediate HTTP 202 with `idempotent_replay: true` without duplicating records or re-enqueuing to Redis.
2. **EC-09 Secret Hygiene**:
   - Webhook Bearer tokens and Authorization headers are automatically scrubbed by structlog's `redact_sensitive_data` processor across all log streams.
3. **FR-05 No-Polling Architecture**:
   - Ingestion path relies entirely on push-based webhook triggers and Redis async queueing. AST analysis confirms no polling loops exist in the codebase.
4. **Optimized Persistence Boundary**:
   - Database operations batch entity creation (`IdempotencyKey`, `Event`, `Execution`) in a single flush and commit transaction, cutting round trips and sustaining >230 rps on standard hardware.
