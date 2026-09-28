# Running the BARQ local PDI demo yourself

This runbook targets the `dev434590` PDI and the API and worker on your Mac.
The seeded Outlook run was repeated on 2026-09-27 and passed against the real
ServiceNow PDI, PostgreSQL, Redis, Celery, Qdrant and Gemini services. The
2026-09-27 trace evidence for earlier runs is in `docs/evidence/`.
Creating an incident in ServiceNow alone does not send it to the local API;
the demo script sends the authenticated webhook event itself.
Do not point this script at the shared `dev407364` instance: its Business Rule
sends the event automatically, and the script's reset would create a second
execution and replace the first suggestion. The script refuses that instance.

The shared EC2 API at `http://51.21.182.56:8000` is a separate deployment from
this local demo. PRs #158 and #174 merged on 2026-09-28. On that date the shared
API returned `/ready` and exposed 18 OpenAPI paths, including the pending
approval and suggestion-decision routes. Those checks prove route availability,
not an end-to-end shared run. Before presenting EC2, run the shared incident
flow and read back the ServiceNow fields, approval row, knowledge article and
Qdrant hit. The local PDI script below is evidence for the local stack only.

The demo script is `scripts/demo_s34_hitl_live.py`. It exercises both halves of
the platform and prints `PASS`/`FAIL` per phase, exiting non-zero if anything
fails, so it works as a gate and not only as a transcript.

---

## 0 · What you need running first

```bash
export PATH=/opt/homebrew/bin:$PATH          # /usr/bin/git and brew tools
cd "/Users/ali-ezz/Downloads/BARQ X Sprints/barq-sprints-agentic-incident-resolution-platform-g1"

open -a Docker                              # the daemon is not running after a reboot
docker compose up -d postgres redis qdrant  # or: docker start barq-postgres barq-redis barq-qdrant
uv sync --all-extras --dev --locked
```

Check them:

```bash
docker ps | grep barq
curl -s http://127.0.0.1:6333/collections/incident_knowledge_base | python3 -m json.tool | head -5
docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" barq-postgres psql -U postgres -d barq_incident_dev -c '\dt'
```

You want: three `barq-*` containers up, a Qdrant collection with a few dozen
points, and seven Postgres tables (`events`, `executions`, `idempotency_keys`,
`workflow_state`, `approvals`, `failures`, `retry_state`).

## 1 · Point the run at the instance you want

Use the test PDI (`dev434590`). Its non-admin integration identity and the
local service settings are in `../.secrets/barq-g1.env`. Source the helper in
**each** terminal; it reads the actual PostgreSQL and Redis passwords from the
running containers and does not print or save credentials:

```bash
. scripts/load_local_demo_env.sh
```

The helper uses the ports exposed by the current local Docker stack. It avoids
depending on a repo `.env` file, which may not exist in a fresh checkout.

## 2 · Start the API and a Celery worker, in two terminals

```bash
. scripts/load_local_demo_env.sh
uv run alembic current
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8099
```

```bash
. scripts/load_local_demo_env.sh
uv run celery -A app.workers.celery_app worker \
  --beat --queues=barq:incident:events,barq:incident:maintenance \
  --loglevel=INFO --pool=threads --concurrency=1
```

**`--pool=threads` is required on macOS.** `--beat` starts the scheduled crash
reaper, and the worker must consume its maintenance queue. With the default
prefork pool every
task dies instantly with
`ValueError: not enough values to unpack (expected 3, got 0)`. That is not an
application bug: `celery/app/trace.py` populates a module-level `_localized`
list during worker startup and reads it in the child, and macOS defaults
`multiprocessing` to `spawn`, so the child starts with an empty list. Linux and
the Docker/EC2 deployment use `fork` and are unaffected.

Confirm both are up:

```bash
curl -s http://127.0.0.1:8099/ready     # {"status":"ready","database":"connected","redis":"connected"}
```

`/ready` checks connectivity, not schema version. Confirm `uv run alembic current`
prints `0003_execution_lease (head)` before running the demo. An older local
database may have the seven Sprint 2 tables but no `alembic_version` row;
`upgrade head` then tries to recreate `events` and fails. Inspect that schema
against migration `0001` before stamping it as `0001_postgresql_state_schema`,
then apply migrations `0002` and `0003`. Never stamp an unknown schema just to
silence the error.

## 3 · Run the demo

Both paths, ~2 minutes:

```bash
. scripts/load_local_demo_env.sh
uv run python scripts/demo_s34_hitl_live.py --all
```

One incident at a time, with your own priority and human solution:

```bash
uv run python scripts/demo_s34_hitl_live.py \
  --incident INC0010023 --priority 1 \
  --solution "Reinstalled the VPN client and flushed the stale route."
```

Stop at the pause and resume by hand — this is the one to use when you want to
show the approval screen to a room:

```bash
uv run python scripts/demo_s34_hitl_live.py --incident INC0010023 --keep-parked
```

The script prints the exact `curl` for the decide call when it stops.

## 4 · What each path proves

**Low/medium risk (`INC0010025`) — straight through.** All eleven nodes run,
`load → validate → classify → determine_risk → retrieve → diagnose → generate →
verify_evidence → safety_check → confidence_check → act`. ServiceNow receives a
numbered, cited draft (`[KB0002 v3.0 §Resolution]`) with a confidence value,
and there is no approval to answer.

**High risk (`INC0010023`, priority 1) — interrupt.** `determine_risk` escalates
*before* retrieval, so the graph calls `interrupt()`, the execution parks at
`awaiting_approval`, and **nothing at all is written to ServiceNow** — not even
the state field. The brief is served from the persisted payload. A decision
resumes the exact checkpoint, and only then does the write land. A second,
contradictory decision is refused with 409.

Phases and the requirement each one answers:

| Phase | Proves | Requirement |
|---|---|---|
| unauthenticated call → 401 | the caller is authenticated before anything else | FR-07 |
| 202 in ~15 ms | no model on the request thread | FR-08, NFR-01 |
| replay flagged idempotent | one event, one execution | FR-09 |
| parked at `awaiting_approval` | a real LangGraph interrupt, not a terminal write | FR-17 |
| incident untouched while parked | no premature ServiceNow write | FR-17, NFR-05 |
| pending approval carries `facts` | the raw audit payload rode the checkpoint | NFR-07 |
| brief present before any decision | brief agent is descriptive only | S3.4 §2 |
| `decided_by` from the token | the body cannot write the audit identity | #148 |
| write lands only after the decision | the write is authorised by the human | NFR-05 |
| `interrupt_resume:…` termination cause | audit separates resume from crash-recovery | S3.4 §4 |
| second decision → 409 | approvals are immutable | S3.4 §3 |

## 5 · The Langfuse trace

Filter the Traces view by `execution_id` — the script prints it. Trace URLs need
a login, so an unauthenticated fetch returns an empty app shell: **screenshot
the trace for a PR, do not paste the link.** Read observations through
`GET /api/public/v2/observations?traceId=…`; the legacy
`/api/public/traces/{id}` returns 410 on this org.

## 6 · Crash-recovery demo (S3.4 §4)

The two kill scenarios are covered by `tests/test_crash_recovery.py`, including
a mutation check: `FakeServiceNow.crash_after_write` dies inside the write
boundary and `test_kill_after_the_write_never_duplicates_it` fails if the
ServiceNow read-back probe is removed. Run them with:

```bash
uv run pytest tests/test_crash_recovery.py tests/test_interrupt_resume.py \
             tests/test_approval_brief.py tests/test_approvals.py -q
```

## 7 · Stopping

```bash
pkill -f "uvicorn app.main:app"
pkill -f "celery -A app.workers.celery_app"
```

Kill the worker before running `pytest -m integration`. A live worker shares the
test queue with a different retry budget and turns six tests into wholesale
failures.

## 8 · If something fails

| Symptom | Cause |
|---|---|
| `not enough values to unpack (expected 3, got 0)` | prefork pool on macOS — use `--pool=threads` |
| webhook returns 401 | `.env` not loaded in that shell, or the token was minted by a different run |
| `no execution appeared` | no worker on `barq:incident:events`, or the incident is not eligible |
| `Not eligible: already processed` | a previous run left it at `awaiting_approval`; the script resets this, a manual `curl` does not |
| psycopg `password authentication failed` | ports/passwords from the wrong source — see §1 |
| suggestion empty on a P1 incident | correct: FR-13 escalates high risk before retrieval, so there is no draft to write |
