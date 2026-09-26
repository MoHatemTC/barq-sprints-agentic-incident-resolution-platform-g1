# Running the BARQ demo yourself

Everything below was run on this machine on 2026-09-26 and passed. It uses the
real stack: the real ServiceNow instance, real PostgreSQL, Redis and Celery,
real Qdrant retrieval, real Gemini through the LiteLLM proxy, real Langfuse.

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

`.env` in the repo is the **shared** instance (`dev407364`). The verified clean
test bed is your own PDI, `dev434590`, whose credentials live in section 6 of
`.secrets/barq-g1.env`. Build a throwaway env file that overrides the ServiceNow
identity — env vars beat `.env` in pydantic-settings, so nothing in the repo
changes:

```bash
set -a; . ../.secrets/barq-g1.env; set +a
umask 077
{ cat .env
  echo "SERVICENOW_INSTANCE_URL='$PDI_INSTANCE_URL'"
  echo "SERVICENOW_CLIENT_ID='$PDI_SERVICENOW_CLIENT_ID'"
  echo "SERVICENOW_CLIENT_SECRET='$PDI_SERVICENOW_CLIENT_SECRET'"
  echo "SERVICENOW_USERNAME='$PDI_SERVICENOW_USERNAME'"
  echo "SERVICENOW_PASSWORD='$PDI_SERVICENOW_PASSWORD'"
  echo "POSTGRES_PORT=5432"
  echo "REDIS_PORT=6379"
} > /tmp/demo.env
set -a; . /tmp/demo.env; set +a
```

> Two gotchas that cost time on 2026-09-26. The local-stack block in
> `.secrets/barq-g1.env` currently says `POSTGRES_PORT=5434` and a 17-character
> password, but the running containers are on **5432** and use the 24-character
> password from the repo `.env`. Nothing listens on 5434. Take the passwords
> from the repo `.env` and the ports from `docker port`, not the other way round.

## 2 · Start the API and a Celery worker, in two terminals

```bash
set -a; . /tmp/demo.env; set +a
uv run uvicorn app.main:app --host 127.0.0.1 --port 8099
```

```bash
set -a; . /tmp/demo.env; set +a
uv run celery -A app.workers.celery_app worker \
  --queues=barq:incident:events --loglevel=INFO --pool=threads --concurrency=1
```

**`--pool=threads` is required on macOS.** With the default prefork pool every
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

## 3 · Run the demo

Both paths, ~2 minutes:

```bash
set -a; . /tmp/demo.env; set +a
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
rm -f /tmp/demo.env
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
