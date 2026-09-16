# Sprint 2 Worker Topology — Redis Queue, Celery, Backoff & Dead-Letter Path (S2.3)

Owner: S2.3 (alfy.kerolous) · Reviewers: Eng. Sarah Nader (mentor), Mohamed (S2.1), Ahmed (S2.2)
Status: implemented on `feat/s2-3-redis-celery-dlq` (stacked on S2.1), suite green including integration.

---

## 1. Scope boundaries

| Component | Owner | This document |
|---|---|---|
| Webhook: auth, validation, idempotent persist, 202 | S2.1 (Mohamed) | Only its enqueue call changed (`refactor(webhook)`) |
| Schema: events / executions / retry_state / failures + CHECKs | S2.2 (Ahmed) | Consumed as-is; every worker write is mapped to a CHECK below |
| Queue, worker, retry/backoff, DLQ, replay | **S2.3** | Full design |
| LangGraph invocation | Sprint 3 | One seam: `app.workers.tasks.invoke_graph` (stub: 0.1s + `INCFAIL*`/`INCBROKEN*` failure injection) |

## 2. Topology

```
ServiceNow ──► POST /api/v1/webhook/incident (S2.1)
                │ bearer auth → idempotent persist (Postgres) → 202 Accepted  (never runs the task)
                │ enqueue ONLY for new events:
                ▼
        app.workers.producer.send_incident_event      ← the single owner of the envelope
                │ Celery JSON envelope {payload, execution_id}, queue barq:incident:events
                ▼
        Redis LIST (broker)  ─ -Q barq:incident:events - acks_late, prefetch=1
                │ single live delivery
                ▼
        celery worker (compose service, prefork ×4, stop_grace_period 30s)
                │ 1. atomic DB claim (terminal/zombie protection)   ← app/workers/db.py
                │ 2. get-or-create retry_state
                │ 3. invoke_graph(payload)                          ← Sprint 3 seam
                │ 4. branch: success / retryable / terminal / unknown
                ▼
        PostgreSQL: executions, retry_state, failures  ← durable truth (FR-15)
                │ on final failure (Task.on_failure)
                ▼
        Redis LIST barq:incident:dlq  ──► humans only: `just dlq-list` / `dlq-replay`
                                           (NO worker ever consumes the DLQ)
```

Key files: `app/workers/{celery_app,tasks,retry_policy,db,sync_engine,producer,replay}.py`.

## 3. Delivery guarantees: at-least-once → exactly-once *effect*

- `task_acks_late=True` + `task_reject_on_worker_lost=True`: a message is acked only after the
  task finishes; a killed worker's message is redelivered.
- `worker_prefetch_multiplier=1`: a hoarded-but-undelivered job is invisible work.
- **Duplicate suppression is the database, not the broker.** The webhook's idempotency key makes
  the same `event_id` un-re-creatable; the worker additionally guards against redelivery of an
  *already-succeeded* event (zombie check) and against *terminal* events (atomic claim).
- The DB claim (`UPDATE … WHERE status IN ('accepted','queued','running') RETURNING …`) protects
  terminal and zombie cases and records state. **Exclusivity between live workers rests on the
  queue's single-delivery semantics** — a deliberate division of labor: the `executions` schema has
  no `claimed_by` column (S2.2 design; listed as future hardening below).

## 4. State machine ↔ CHECK-constraint map

Every transition the worker can make, and the schema constraint it satisfies (constraints defined
by S2.2 in `app/db/models.py`):

| Transition (code path) | executions | retry_state | Constraints satisfied |
|---|---|---|---|
| pickup | accepted/queued → `running` (atomic claim, RETURNING) | get-or-create `('ready', 0, max)` | `status` CHECK; claim refuses terminal |
| transient fail, budget left | running → `queued` (backoff = nobody working) | attempt_count=n, `scheduled`, backoff_seconds, next_retry_at, last_failure_id | `active_retry_remaining` (n < max); `last_attempt_consistency`; `schedule_time`; executions non-terminal ⇒ ended_at/cause NULL |
| transient fail, last attempt | → `failed` + ended_at + cause | `exhausted`, attempt_count=**max**, next_retry_at=NULL | `exhausted_attempt_limit` (exhausted ⟺ count == max); `terminal_state` |
| terminal / unknown error | → `failed` + ended_at + cause | `cancelled`, attempt_count = **the attempt that failed** | `exhausted_attempt_limit` (1 ≤ count < max); honest history — never forged to max; `terminal_state` |
| success | → `succeeded` + ended_at + `termination_cause='completed'` | `succeeded`, next_retry_at=NULL | `terminal_state` |
| replay (CLI, guarded) | parked → `queued`, ended_at/cause NULLed | → `ready`, count=0, last_attempt_at/next_retry_at NULLed | atomic `WHERE state IN ('exhausted','cancelled')` — 0 rows ⇒ refuse; resets happen together per `last_attempt_consistency` |
| redelivery after hard kill | `running` → `running` self-transition | unchanged | claim allows running→running (see §8) |

`failures` rows are written per attempt through a **fresh short-lived session** (the session that
witnessed the failure is dead — it aborted with the task's transaction).

## 5. Retry policy (`app/workers/retry_policy.py`)

- **Classification, fail closed**: `RetryableError` (timeout/connection/soft-time-limit) retries;
  `TerminalError` and **any unknown exception** are terminal. Retrying a deterministic bug only
  hides it. `SoftTimeLimitExceeded` is wrapped into a retryable error and its attempt IS logged.
- **No `autoretry_for`.** The task calls `raise self.retry(exc=exc, countdown=delay)` explicitly —
  Celery's ETA is *constructed from the same delay* that is written to
  `retry_state.next_retry_at`, so DB schedule and broker schedule cannot disagree (no second
  opinion from autoretry).
- **Backoff**: `delay = min(base · 2^(attempt−1), max)` with **full jitter** on top
  (`uniform(0, uncapped)`) so parallel retriers don't sync up. Jitter off ⇒ exact formula
  (deterministic tests/demo).
- Config comes entirely from `Settings` (`worker_*` fields); the worker code contains no literal
  retry/timeout/concurrency numbers.

## 6. Dead-letter path & replay (`app/workers/replay.py`)

- The DLQ is a **bulletin board for humans**: a JSON record per final failure with the fields of
  S2.1's `DLQEventResponse` (`event_id`, `payload`, `failure_reason`, `retry_count`, `failed_at`).
  Written Redis-**first** (survives a Postgres outage — separate dependency), then stderr, then a
  best-effort DB reconciliation for state the in-body handler could not record.
- Replay order: read original payload from the immutable `events` row → atomic
  `reset_for_replay` (refuses anything not parked) → **LREM** the event's records by exact string
  (a fresh failure carries a new `failed_at`, so a concurrent re-push can never match an old
  string) → re-enqueue **through the one producer**. If the process dies between reset and
  enqueue, recovery is the documented sweep (`events WHERE status='queued'`).
- Postgres is the durable truth; the Redis list is a parking lot. Replay of an event whose root
  cause persists fails honestly again (transcript: budget 3 → 6 failure rows preserved).

## 7. Operational constraints (read before touching config)

1. **`worker_max_retries` must be identical for the replay CLI and the consuming worker.** The
   database pairs `attempt_count` with `max_attempts` (`ck_retry_state_exhausted_attempt_limit`):
   if the CLI reset an event with max=5 but the worker's budget is 3, the exhaustion write
   (count=3, row max=5) violates the CHECK. The walkthrough script exports one configuration for
   every process it spawns. *Proposed follow-up guard:* `ensure_retry_state` may align
   `max_attempts` to the worker's config only when the row is `('ready', count=0)`.
2. **`stop_grace_period: 30s` < `worker_soft_time_limit: 120s` is intentionally safe**: a task
   killed at grace expiry is recovered by redelivery + the running→running claim, not by grace.
   Grace only shortens the *worst case* container shutdown; correctness comes from acks_late.
   Revisit if Sprint 3 graph runs become long enough that re-running them is expensive.
3. **redis-py is pinned to 5.2.1**: 8.x breaks kombu 5.6 BRPOP parsing
   (`KeyError: 'properties'` on delivery — reproduced live). Revisit when kombu supports 8.x.
4. ~~`create_celery_app` currently does not apply `worker_concurrency`~~ **Fixed**: the celery app
   now applies `worker_concurrency` from Settings (a `--concurrency` CLI flag still overrides), so
   a bare local `celery worker` no longer spawns CPU-count children.

## 8. Race guards summary

| Race | Guard |
|---|---|
| Same event delivered twice (ServiceNow retry) | idempotency key UNIQUE — DB arbitrates, no check-then-insert |
| Two live workers get the same message | Redis list BRPOP single-delivery (queue's job) |
| Redelivery after worker death (SIGKILL/OOM) | acks_late + reject_on_worker_lost; claim self-transition `running→running` reruns it; `redelivered` flag is NOT reliable on kombu/Redis (experimentally verified) and is not used |
| Message redelivered after success | zombie guard: status `succeeded` ⇒ no-op return |
| Terminal event replayed / re-enqueued | claim refuses terminal; replay refuses non-parked |
| Failure logged from a dead session | fresh session per repo operation |
| Replay LREM racing a fresh DLQ push | exact-string LREM; new record ⇒ new `failed_at` ⇒ different string |

## 9. Configuration (defaults; every value config-driven)

| Setting | Default | Why |
|---|---|---|
| `worker_max_retries` | 5 | Total attempts per event; written into `retry_state.max_attempts` so the DB enforces the same budget |
| `worker_backoff_base` / `_max` | 1.0s / 60s | delay = base·2^(n−1) capped; sized for sub-90s graph runs |
| `worker_backoff_jitter` | true | full jitter against thundering-herd retries |
| `worker_soft_time_limit` / `_time_limit` | 120s / 150s | soft raises inside the task (→ retryable); hard SIGKILLs; hard > soft leaves cleanup room |
| `worker_prefetch` | 1 | long tasks must not sit behind hoarded jobs |
| `worker_concurrency` | 4 | applied in the celery app config; a `--concurrency` CLI flag still overrides |
| `worker_repo_backend` | postgres | `memory` exists for fast tests only |
| `worker_max_tasks_per_child` | 1000 | bounds prefork child memory growth |

## 10. Version pins (evidence in `docs/sprint2_test_output.txt`)

celery 5.6.3 · kombu 5.6.2 · redis 5.2.1 (pinned, §7.3) · psycopg 3.3.5 · SQLAlchemy 2.0.53.

## 11. Measured evidence (2026-09-16, dev stack; see committed artifacts)

- **Exponential backoff, measured from the database** (`failures.occurred_at` LAG): with
  base=0.5s, jitter off → gaps **0.656s / 1.152s** ≈ stub sleep 0.1s + delays 0.5s/1.0s
  (`docs/sprint2_cli_walkthrough.md` step 3).
- **SIGTERM warm shutdown, mid-drain** (300 events seeded, worker at concurrency 4 killed after
  ≥4 succeeded): snapshot before `succeeded=4, pending=295, running=1`; after clean exit
  `succeeded=8, pending=292, running=0` — **in-flight finished, unfetched stayed queued,
  all 300 accounted, zero loss**; log line `worker: Warm shutdown (MainProcess)`; full drain
  after restart.
- **Webhook 202 latency** (200 sequential deliveries, single connection — conservative baseline,
  not a load-test claim): p50 19.4ms · **p95 22.7ms** · p99 26.4ms vs NFR-01's 500ms budget.
  Sustained saturation p95 is a joint run with S2.1 — pending coordination.
- **Worker throughput** (stub graph, concurrency 4): 200 events in 10.1s ≈ 20/s including worker
  boot (theoretical ceiling ≈ 40/s at the 0.1s stub). Real numbers arrive with Sprint 3's graph.

## 12. Test inventory

| Layer | File(s) | What it pins |
|---|---|---|
| Unit: policy | `tests/workers/test_retry_policy.py` | backoff formula incl. jitter bounds; unknown → terminal (fail closed); config-driven |
| Unit: topology | `tests/workers/test_celery_app.py` | broker URL (SecretStr, empty-password safe), queues, acks_late, JSON-only |
| Contract | `tests/workers/repo_contract.py` + in-memory/pg backends | every repo operation against both backends incl. replay refusals and event lookups |
| Eager task | `tests/workers/test_tasks_eager.py` | state machine, unified soft-time-limit handler, DLQ fields, delay==countdown consistency |
| Producer | `tests/workers/test_producer.py` | single envelope owner: routing, task name, UUID coercion |
| Replay | `tests/workers/test_replay.py` | refusal paths, LREM, newest-first listing, garbage tolerance |
| **Integration** | `tests/workers/test_integration.py` (`pytest -m integration`) | real broker+DB+worker subprocess: duplicate → one execution, measured backoff, DLQ end-to-end, replay of both parked states, poison isolation, zombie no-op, kill-redelivery self-transition, terminal refusals |
| Ops demo | `scripts/s2_3_cli_walkthrough.sh` → `docs/sprint2_cli_walkthrough.md` | full story via CLIs (the transcript for review) |

## 13. Known limitations / future hardening

1. No `claimed_by` column: a worker that dies *and* loses its message simultaneously leaves
   `running` until redelivery or manual sweep (schema change is S2.2's call).
2. Redis-loss recovery (re-enqueue sweep from `events WHERE status='queued'`) is documented but
   unimplemented (stretch; AOF is already on in compose).
3. Saturation p95 for the webhook is a joint run with S2.1 (§11).
4. `worker_max_retries` drift guard (§7.1) — small follow-up commit.
