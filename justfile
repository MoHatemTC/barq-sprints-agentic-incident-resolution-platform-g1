set shell := ["bash", "-cu"]

default:
    @just --list

install:
    uv sync --locked

run:
    uv run uvicorn app.main:app --reload

test:
    uv run pytest

test-cov:
    uv run pytest --cov=app

lint:
    uv run ruff check .

format:
    uv run ruff format .

fix:
    uv run ruff check . --fix
    uv run ruff format .

typecheck:
    uv run mypy src

precommit:
    uv run pre-commit run --all-files  

# Everything CI runs, in the same order.
check:
    uv lock --check
    just lint
    uv run ruff format --check .
    just typecheck
    just test

# ── S2.3 — queue, workers, dead-letter path ────────────────────────────────

# Local celery worker against the docker postgres/redis (stop the compose
# celery-worker first if it is running, or the two workers will share the queue).
worker:
    uv run celery -A app.workers.celery_app worker --queues=barq:incident:events --loglevel=INFO

# Queue depths — executed inside the container so credentials come from compose env.
redis-events:
    docker compose exec -T redis sh -c 'test -n "$REDIS_PASSWORD" && export REDISCLI_AUTH="$REDIS_PASSWORD"; redis-cli llen barq:incident:events'

redis-dlq:
    docker compose exec -T redis sh -c 'test -n "$REDIS_PASSWORD" && export REDISCLI_AUTH="$REDIS_PASSWORD"; redis-cli llen barq:incident:dlq'

redis-dlq-peek:
    docker compose exec -T redis sh -c 'test -n "$REDIS_PASSWORD" && export REDISCLI_AUTH="$REDIS_PASSWORD"; redis-cli lrange barq:incident:dlq 0 -1'

# Dead-letter inspection / replay CLI (S2.3).
dlq-list:
    uv run python -m app.workers.replay list

dlq-replay EVENT_ID:
    uv run python -m app.workers.replay replay {{EVENT_ID}}

psql-executions:
    docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT execution_id, status, started_at, ended_at, termination_cause FROM executions ORDER BY started_at DESC LIMIT 20;"'

psql-failures:
    docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT execution_id, attempt, failure_type, retryable, occurred_at, message FROM failures ORDER BY occurred_at DESC LIMIT 20;"'

psql-retry-state:
    docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT execution_id, state, attempt_count, max_attempts, backoff_seconds, next_retry_at, last_attempt_at FROM retry_state ORDER BY last_attempt_at DESC NULLS LAST LIMIT 20;"'

test-workers:
    uv run pytest tests/workers -q

test-integration:
    uv run pytest -m integration -q

# Full S2.3 live demo (success, duplicate, backoff, DLQ, replay) — the
# transcript doubles as review evidence; see docs/sprint2_cli_walkthrough.md.
walkthrough:
    bash scripts/s2_3_cli_walkthrough.sh

