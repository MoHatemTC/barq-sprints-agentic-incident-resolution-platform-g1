#!/usr/bin/env bash
# =============================================================================
# S2.3 CLI walkthrough — live evidence of the queue / worker / backoff / DLQ
# path, driven entirely through inspectable CLIs (redis-cli, psql, the replay
# tool). The transcript doubles as review evidence; see
# docs/sprint2_cli_walkthrough.md.
#
# Infrastructure: the docker postgres + redis. The worker is started LOCALLY
# with a test-scaled, deterministic configuration (budget 3, base 0.5s, no
# jitter → delays 0.5s then 1.0s) so the walkthrough finishes in seconds; the
# production-sized defaults are already exercised by the compose service.
#
# Usage:  just walkthrough     (or: bash scripts/s2_3_cli_walkthrough.sh)
# Requires: docker compose postgres+redis healthy, .env present.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

# One source of truth for EVERY process in this demo (worker, seeding, replay
# CLI): a scaled deterministic budget. The replay CLI and the worker MUST run
# with the same worker_max_retries — the database pairs attempt_count with
# max_attempts (ck_retry_state_exhausted_attempt_limit).
export WEBHOOK_AUTH_TOKEN="${WEBHOOK_AUTH_TOKEN:-walkthrough}"
export WORKER_MAX_RETRIES=3
export WORKER_BACKOFF_BASE=0.5
export WORKER_BACKOFF_MAX=5
export WORKER_BACKOFF_JITTER=false

step() { printf '\n════════════════════════════════════════════════════════════════\n  %s\n════════════════════════════════════════════════════════════════\n' "$*"; }

psql_scalar() { # single value
  docker compose exec -T postgres sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc \"$1\""
}
psql_show() { # aligned table
  docker compose exec -T postgres sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c \"$1\""
}
redis_exec() {
  docker compose exec -T redis sh -c "test -n \"\$REDIS_PASSWORD\" && export REDISCLI_AUTH=\"\$REDIS_PASSWORD\"; redis-cli $1"
}

# ── preflight ───────────────────────────────────────────────────────────────
docker compose exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null
redis_exec ping | grep -q PONG
echo "postgres + redis are up."
redis_exec 'del barq:incident:events barq:incident:dlq' >/dev/null
echo "queues cleared for a clean run."

COMPOSE_WORKER_WAS_RUNNING=0
if [ -n "$(docker compose ps -q celery-worker)" ]; then
  COMPOSE_WORKER_WAS_RUNNING=1
  docker compose stop celery-worker >/dev/null
  echo "note: compose celery-worker stopped for a deterministic solo demo (restarted on exit)."
fi

# ── worker under test ───────────────────────────────────────────────────────
step "Starting the local celery worker (scaled config: retries=3, base=0.5s, jitter=off)"
WORKER_LOG="$(mktemp /tmp/barq-walkthrough-worker.XXXXXX.log)"
uv run celery -A app.workers.celery_app worker \
  --pool=solo --concurrency=1 --queues=barq:incident:events \
  --loglevel=WARNING --without-gossip --without-mingle --without-heartbeat \
  >"$WORKER_LOG" 2>&1 &
WORKER_PID=$!

cleanup() {
  if [ -n "${WORKER_PID:-}" ]; then
    kill "$WORKER_PID" 2>/dev/null || true
    for _ in $(seq 1 20); do kill -0 "$WORKER_PID" 2>/dev/null || break; sleep 0.5; done
    kill -9 "$WORKER_PID" 2>/dev/null || true
  fi
  if [ "$COMPOSE_WORKER_WAS_RUNNING" = 1 ]; then
    docker compose start celery-worker >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

uv run python - <<'PY'
import time
from app.workers.celery_app import celery_app

for _ in range(120):
    if celery_app.control.ping(timeout=0.5):
        print("worker answered inspect ping.")
        break
    time.sleep(0.5)
else:
    raise SystemExit("worker did not come up within 60s")
PY

seed_and_send() { # $1 = incident number; prints "<event_id>\n<execution_id>"
  uv run python - "$1" <<'PY'
import sys
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Event, Execution
from app.workers.producer import send_incident_event
from app.workers.sync_engine import (
    build_sync_database_url,
    create_sync_engine,
)

number = sys.argv[1]
payload = {
    "event_id": f"evt-walkthrough-{uuid4().hex[:12]}",
    "sys_id": uuid4().hex[:16],
    "number": number,
    "event_type": "incident.created",
    "contract_version": "v1",
}
engine = create_sync_engine(build_sync_database_url(get_settings()))
with Session(engine) as session, session.begin():
    event = Event(
        event_id=payload["event_id"],
        incident_sys_id=payload["sys_id"],
        incident_number=number,
        event_type="incident.created",
        contract_version="v1",
    )
    session.add(event)
    session.flush()
    execution = Execution(
        event_record_id=event.id,
        incident_sys_id=payload["sys_id"],
        status="accepted",
    )
    session.add(execution)
    session.flush()
    execution_id = str(execution.execution_id)
engine.dispose()
send_incident_event(payload, execution_id)
print(payload["event_id"])
print(execution_id)
PY
}

await_status() { # $1 = execution_id, $2 = expected status
  local status=""
  for _ in $(seq 1 120); do
    status="$(psql_scalar "SELECT status FROM executions WHERE execution_id='$1'")"
    [ "$status" = "$2" ] && return 0
    sleep 0.25
  done
  echo "timed out waiting for status '$2' (last: '$status')" >&2
  exit 1
}

await_dlq_count() { # $1 = event_id, $2 = expected record count
  local count=""
  for _ in $(seq 1 120); do
    count="$(uv run python - "$1" <<'PY'
import sys

import redis

from app.core.config import get_settings
from app.workers.replay import load_dead_letters

settings = get_settings()
password = (
    settings.redis_password.get_secret_value()
    if settings.redis_password is not None
    else None
)
client = redis.Redis(
    host=settings.redis_host,
    port=settings.redis_port,
    password=password,
    decode_responses=True,
)
records = load_dead_letters(client)
print(sum(1 for r in records if r.get("event_id") == sys.argv[1]))
PY
)"
    [ "$count" = "$2" ] && return 0
    sleep 0.25
  done
  echo "timed out waiting for $2 DLQ record(s) for $1 (last: $count)" >&2
  exit 1
}

# ── step 1 ──────────────────────────────────────────────────────────────────
step "1/7 — healthy event: webhook payload → queue → worker → succeeded"
OUT="$(seed_and_send "INC7a1b")"
EVENT_ID="$(echo "$OUT" | sed -n 1p)"
EXEC_ID="$(echo "$OUT" | sed -n 2p)"
echo "enqueued event $EVENT_ID (execution $EXEC_ID)"
await_status "$EXEC_ID" "succeeded"
psql_show "SELECT status, started_at, ended_at, termination_cause FROM executions WHERE execution_id='$EXEC_ID'"

# ── step 2 ──────────────────────────────────────────────────────────────────
step "2/7 — duplicate delivery: the same event_id twice → exactly one execution"
DUP_OUT="$(uv run python - <<'PY'
import asyncio
from uuid import uuid4

from app.core.config import get_settings
from app.db.session import create_db_engine, create_session_factory
from app.repositories.idempotency import InboundEvent, accept_inbound_event


async def main():
    engine = create_db_engine(get_settings())
    factory = create_session_factory(engine)
    inbound = InboundEvent(
        event_id=f"evt-walkthrough-dup-{uuid4().hex[:8]}",
        sys_id=uuid4().hex[:16],
        number=f"INC777{uuid4().hex[:8]}",
        event_type="incident.created",
    )
    try:
        first = await accept_inbound_event(factory, inbound)
        second = await accept_inbound_event(factory, inbound)
        print(inbound.event_id)
        print(f"first  delivery: {first.status.value:<9} execution={first.execution_id}")
        print(f"second delivery: {second.status.value:<9} execution={second.execution_id}")
    finally:
        await engine.dispose()


asyncio.run(main())
PY
)"
DUP_EVENT_ID="$(echo "$DUP_OUT" | sed -n 1p)"
echo "$DUP_OUT" | sed -n '2,3p'
echo "events rows for that event_id: $(psql_scalar "SELECT count(*) FROM events WHERE event_id='$DUP_EVENT_ID'") (unique constraint arbitrated — no check-then-insert race)"

# ── step 3 ──────────────────────────────────────────────────────────────────
step "3/7 — transient failure: 3 attempts, exponential backoff (0.5s, 1.0s), then dead-letter"
OUT="$(seed_and_send "INCFAIL$RANDOM$RANDOM")"
FAIL_EVENT_ID="$(echo "$OUT" | sed -n 1p)"
FAIL_EXEC_ID="$(echo "$OUT" | sed -n 2p)"
echo "enqueued $FAIL_EVENT_ID (execution $FAIL_EXEC_ID)"
await_status "$FAIL_EXEC_ID" "failed"
await_dlq_count "$FAIL_EVENT_ID" 1
echo "measured gaps between failures.occurred_at (expect ≈ stub sleep 0.1s + delay):"
psql_show "SELECT attempt, failure_type, retryable, occurred_at, ROUND(EXTRACT(EPOCH FROM (occurred_at - LAG(occurred_at) OVER (ORDER BY occurred_at)))::numeric, 3) AS gap_seconds, message FROM failures WHERE execution_id='$FAIL_EXEC_ID' ORDER BY occurred_at"
psql_show "SELECT state, attempt_count, max_attempts, next_retry_at FROM retry_state WHERE execution_id='$FAIL_EXEC_ID'"
psql_show "SELECT status, ended_at, termination_cause FROM executions WHERE execution_id='$FAIL_EXEC_ID'"
echo "DLQ depth: $(redis_exec 'llen barq:incident:dlq')"

# ── step 4 ──────────────────────────────────────────────────────────────────
step "4/7 — poison event: terminal error → cancelled on attempt 1 (budget NOT forged)"
OUT="$(seed_and_send "INCBROKEN$RANDOM$RANDOM")"
POISON_EVENT_ID="$(echo "$OUT" | sed -n 1p)"
POISON_EXEC_ID="$(echo "$OUT" | sed -n 2p)"
echo "enqueued $POISON_EVENT_ID (execution $POISON_EXEC_ID)"
await_status "$POISON_EXEC_ID" "failed"
await_dlq_count "$POISON_EVENT_ID" 1
psql_show "SELECT state, attempt_count, max_attempts FROM retry_state WHERE execution_id='$POISON_EXEC_ID'"
psql_show "SELECT attempt, failure_type, retryable, message FROM failures WHERE execution_id='$POISON_EXEC_ID' ORDER BY attempt"

# ── step 5 ──────────────────────────────────────────────────────────────────
step "5/7 — inspecting the DLQ (bulletin board for humans — no worker consumes it)"
uv run python -m app.workers.replay list

# ── step 6 ──────────────────────────────────────────────────────────────────
step "6/7 — replay the EXHAUSTED event: fresh budget, history preserved"
uv run python -m app.workers.replay replay "$FAIL_EVENT_ID"
await_dlq_count "$FAIL_EVENT_ID" 1   # fails again (stub always fails), parks once more
await_status "$FAIL_EXEC_ID" "failed"
echo "failure history after replay (1st run rows kept, 2nd run appended):"
psql_show "SELECT attempt, occurred_at, message FROM failures WHERE execution_id='$FAIL_EXEC_ID' ORDER BY occurred_at"
echo "total failure rows: $(psql_scalar "SELECT count(*) FROM failures WHERE execution_id='$FAIL_EXEC_ID'") (was 3 — the replay really did reset and re-run)"
echo "DLQ depth: $(redis_exec 'llen barq:incident:dlq')"

# ── step 7 ──────────────────────────────────────────────────────────────────
step "7/7 — replay the CANCELLED event: it runs again and, being poison, cancels honestly"
uv run python -m app.workers.replay replay "$POISON_EVENT_ID"
await_status "$POISON_EXEC_ID" "failed"
echo "retry_state after replaying a cancelled event:"
psql_show "SELECT state, attempt_count, max_attempts FROM retry_state WHERE execution_id='$POISON_EXEC_ID'"
echo "failure rows: $(psql_scalar "SELECT count(*) FROM failures WHERE execution_id='$POISON_EXEC_ID'") (one per replay run)"

# ── summary ─────────────────────────────────────────────────────────────────
step "Walkthrough complete"
echo "worker log: $WORKER_LOG"
echo "inspect further with: just psql-executions / psql-failures / psql-retry-state / redis-dlq-peek / dlq-list"
