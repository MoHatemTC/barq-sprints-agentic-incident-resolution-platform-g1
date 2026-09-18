"""Incident-processing task (S2.3) — the execution substrate's entry point.

Flow per attempt:

1. Atomic claim (zombie + terminal protection — see db.WorkerRepo).
2. Get-or-create retry_state (``accept_inbound_event`` does not create it).
3. ``invoke_graph`` — runs the S2.5 LangGraph state machine
   (``agent.runtime.invoke_incident_graph``) when ``AGENT_GRAPH_BACKEND=langgraph``
   (the default). ``stub`` keeps the simulated work and its forced-failure magic
   numbers for the failure-injection tests and demo.
4. Exception handling, in exactly three branches:
   - ``(RetryableError, SoftTimeLimitExceeded)``: log the attempt, then either
     schedule a retry (explicit ``self.retry`` with THE delay — the same delay
     is recorded in ``retry_state.next_retry_at``) or, on the final attempt,
     mark exhausted and raise through to ``on_failure`` (DLQ).
   - ``TerminalError``: log, mark cancelled, raise through to ``on_failure``.
   - ``Exception`` (unknown): fail closed — same as terminal. Retrying a
     deterministic bug only hides it.
5. Success: executions 'succeeded' with ended_at + termination_cause
   (ck_executions_terminal_state) and retry_state 'succeeded'.

Each repo call opens its own short-lived transaction (see db.py): failure
logging never reuses the session that died with the work.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any
from uuid import UUID

import redis as redis_lib
import structlog
from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import worker_process_shutdown

from agent.config import get_agent_settings
from app.core.config import Settings, get_settings
from app.core.correlation import clear_correlation_id, set_correlation_id
from app.db.redis.keys import INCIDENT_DLQ_QUEUE
from app.workers.celery_app import celery_app
from app.workers.db import WorkerRepo, build_worker_repo
from app.workers.producer import CORRELATION_HEADER
from app.workers.retry_policy import (
    RetryableError,
    RetryConfig,
    TerminalError,
    backoff_delay,
    build_retry_config,
)
from observability.tracing import get_tracer

logger = structlog.getLogger(__name__)

# Stub-graph scaffolding (S2.3): a fake processing step with failure-injection
# magic numbers. Replaced by the LangGraph invocation in Sprint 3.
GRAPH_STUB_SLEEP_SECONDS = 0.1

_TERMINAL_EXECUTION_STATUSES = ("succeeded", "failed", "blocked", "abandoned")


def invoke_graph(
    payload: dict[str, Any],
    *,
    backend: str = "stub",
    execution_id: str | None = None,
    attempt: int = 1,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """The graph seam: the S2.5 LangGraph state machine, or S2.3's stub.

    ``backend`` defaults to the stub so the S2.3 state-machine tests can drive
    ``_run_incident`` without live services; the registered task passes the
    configured ``AGENT_GRAPH_BACKEND``.
    """
    if backend == "langgraph":
        from agent.runtime import invoke_incident_graph

        return invoke_incident_graph(
            payload,
            execution_id=str(execution_id),
            correlation_id=correlation_id or str(execution_id),
            attempt=attempt,
        )
    return _stub_graph(payload)


def _stub_graph(payload: dict[str, Any]) -> dict[str, Any]:
    """Simulates the agent graph: takes ~0.1s, and raises the configured failure
    classes for the failure-injection demo numbers."""
    time.sleep(GRAPH_STUB_SLEEP_SECONDS)

    number = str(payload.get("number", ""))
    if number.startswith("INCFAIL"):
        raise RetryableError(f"forced transient failure for {number}")
    if number.startswith("INCBROKEN"):
        raise TerminalError(f"forced terminal failure for {number}")
    return {"draft": f"stub resolution for {number}"}


def record_dead_letter(
    repo: WorkerRepo,
    redis_sink: Any,
    payload: dict[str, Any],
    execution_id: str,
    exc: Exception,
    attempt: int,
) -> None:
    """Persist the DLQ record. Redis FIRST (it survives a Postgres outage —
    they are separate dependencies), stderr second, then a best-effort DB
    reconciliation for any state the in-body handlers could not record."""
    reason = str(exc)
    record: dict[str, Any] = {
        "event_id": str(payload.get("event_id", "")),
        "payload": payload,
        "failure_reason": reason,
        "retry_count": attempt,
        "failed_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    try:
        redis_sink.lpush(INCIDENT_DLQ_QUEUE, json.dumps(record))
    except Exception:  # noqa: BLE001 — Redis down must not prevent DB reconciliation
        logger.error(
            "dead_letter_redis_push_failed",
            event_id=record["event_id"],
            reason=reason,
        )
    else:
        logger.warning("dead_lettered", event_id=record["event_id"], reason=reason)

    try:
        execution_uuid = UUID(execution_id)
        status = repo.get_status(execution_uuid)
        if status not in _TERMINAL_EXECUTION_STATUSES:
            # The in-body handlers could not finish (e.g. Postgres was down).
            # Record the failure row and park the state now, best-effort.
            failure_id = repo.log_failure(
                execution_id=execution_uuid,
                attempt=attempt,
                failure_type="unhandled_on_failure",
                message=reason,
                retryable=False,
            )
            repo.mark_cancelled(
                execution_id=execution_uuid,
                attempt=attempt,
                last_failure_id=failure_id,
                termination_cause=reason,
            )
    except Exception:  # noqa: BLE001 — the DLQ write already succeeded
        logger.error("dead_letter_db_reconciliation_failed", event_id=record["event_id"])


class IncidentTask(Task):
    """Task base carrying the dependencies and the DLQ failure hook."""

    settings: Settings
    repo: WorkerRepo
    dlq_redis: Any

    def on_failure(
        self,
        exc: Exception,
        task_id: str,
        args: tuple,
        kwargs: dict,
        einfo: Any,
    ) -> None:
        payload = (args or (None,))[0] or {}
        execution_id = str((args or (None, None))[1] or "")
        attempt = int(getattr(self.request, "retries", 0)) + 1
        record_dead_letter(self.repo, self.dlq_redis, payload, execution_id, exc, attempt)


def _failure_type(exc: BaseException) -> str:
    if isinstance(exc, SoftTimeLimitExceeded):
        return "soft_time_limit"
    return type(exc).__name__


def _run_incident(
    task: Task,
    payload: dict[str, Any],
    execution_id: str,
    cfg: RetryConfig,
    repo: WorkerRepo,
    soft_time_limit_seconds: int = 120,
    correlation_id: str | None = None,
    graph_backend: str = "stub",
) -> dict[str, Any]:
    """One delivery attempt. ``task`` is the bound Celery task (or a fake in
    tests) providing ``request.retries`` and ``retry()``."""
    execution_uuid = UUID(execution_id)
    attempt = int(task.request.retries) + 1

    # Zombie guard: at-least-once delivery can redeliver a message whose event
    # already succeeded — never re-run the graph for those.
    status = repo.get_status(execution_uuid)
    if status == "succeeded":
        logger.info("zombie_redelivery_skipped", execution_id=execution_id)
        return {"status": "already_done", "execution_id": execution_id}

    if not repo.claim_for_running(execution_uuid):
        # Terminal or unknown: not claimable (replay resets the state first).
        logger.warning("claim_refused", execution_id=execution_id, status=status)
        return {"status": "skipped", "execution_id": execution_id}

    repo.ensure_retry_state(execution_uuid, max_attempts=cfg.max_retries)

    try:
        result = invoke_graph(
            payload,
            backend=graph_backend,
            execution_id=execution_id,
            attempt=attempt,
            correlation_id=correlation_id,
        )
    except (RetryableError, SoftTimeLimitExceeded) as exc:
        if isinstance(exc, SoftTimeLimitExceeded):
            wrapped = RetryableError(f"soft time limit exceeded after {soft_time_limit_seconds}s")
            wrapped.__cause__ = exc
            exc = wrapped

        # The attempt is logged no matter what happens next (fresh session).
        failure_id = repo.log_failure(
            execution_id=execution_uuid,
            attempt=attempt,
            failure_type=_failure_type(exc),
            message=str(exc),
            retryable=True,
        )

        if attempt < cfg.max_retries:
            delay = backoff_delay(attempt, cfg)
            repo.schedule_retry(
                execution_id=execution_uuid,
                attempt=attempt,
                backoff_seconds=delay,
                next_retry_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=delay),
                last_failure_id=failure_id,
            )
            # Explicit retry: the countdown IS the delay recorded above —
            # one source of truth, no autoretry_for second opinion.
            raise task.retry(exc=exc, countdown=delay) from exc

        # Budget consumed: exhausted ⟺ attempt_count == max_attempts.
        repo.mark_exhausted(
            execution_id=execution_uuid,
            max_attempts=cfg.max_retries,
            last_failure_id=failure_id,
        )
        logger.warning("retry_budget_exhausted", execution_id=execution_id, attempts=attempt)
        raise exc from exc
    except TerminalError as exc:
        failure_id = repo.log_failure(
            execution_id=execution_uuid,
            attempt=attempt,
            failure_type=_failure_type(exc),
            message=str(exc),
            retryable=False,
        )
        repo.mark_cancelled(
            execution_id=execution_uuid,
            attempt=attempt,
            last_failure_id=failure_id,
            termination_cause=f"terminal failure: {exc}",
        )
        logger.warning("terminal_failure", execution_id=execution_id, reason=str(exc))
        raise
    except Exception as exc:
        # Unknown exception: fail closed. A deterministic bug gets no retries.
        failure_id = repo.log_failure(
            execution_id=execution_uuid,
            attempt=attempt,
            failure_type=_failure_type(exc),
            message=str(exc),
            retryable=False,
        )
        repo.mark_cancelled(
            execution_id=execution_uuid,
            attempt=attempt,
            last_failure_id=failure_id,
            termination_cause=f"unclassified failure: {exc}",
        )
        logger.error("unclassified_failure", execution_id=execution_id, reason=str(exc))
        raise

    repo.mark_succeeded(execution_uuid)
    logger.info("incident_processed", execution_id=execution_id)
    return {"status": "succeeded", "execution_id": execution_id, "result": result}


def build_incident_task(
    app: Any,
    settings: Settings,
    repo: WorkerRepo | None = None,
    dlq_redis: Any = None,
) -> Any:
    """Build the task bound to a specific app/settings — the factory exists so
    tests (and alternative deployments) can inject configuration and backends."""
    graph_backend = get_agent_settings().agent_graph_backend
    cfg = build_retry_config(settings)
    repo = repo if repo is not None else build_worker_repo(settings)
    if dlq_redis is None:
        password = (
            settings.redis_password.get_secret_value()
            if settings.redis_password is not None
            else None
        )
        dlq_redis = redis_lib.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=password,
            decode_responses=True,
        )

    @app.task(
        bind=True,
        name="app.workers.tasks.process_incident",
        base=IncidentTask,
        max_retries=cfg.max_retries,
        autoretry_for=(),
        soft_time_limit=settings.worker_soft_time_limit,
        time_limit=settings.worker_time_limit,
    )
    def process_incident(self: IncidentTask, payload: dict[str, Any], execution_id: str) -> dict:
        self.settings = settings
        self.repo = repo
        self.dlq_redis = dlq_redis
        correlation_id = correlation_id_from(self.request) or execution_id
        attempt = int(self.request.retries) + 1
        tracer = get_tracer()
        set_correlation_id(correlation_id)
        try:
            with (
                tracer.span(
                    "worker.pickup",
                    correlation_id=correlation_id,
                    as_type="agent",
                    input={"event_id": payload.get("event_id"), "attempt": attempt},
                    metadata={
                        "execution_id": execution_id,
                        "incident_number": payload.get("number"),
                        "attempt": attempt,
                        "graph_backend": graph_backend,
                    },
                ) as span,
                tracer.trace_attributes(
                    correlation_id=correlation_id,
                    incident_number=str(payload.get("number") or "") or None,
                    execution_id=execution_id,
                ),
            ):
                result = _run_incident(
                    self,
                    payload,
                    execution_id,
                    cfg=cfg,
                    repo=repo,
                    soft_time_limit_seconds=settings.worker_soft_time_limit,
                    correlation_id=correlation_id,
                    graph_backend=graph_backend,
                )
                span.update(output=result)
                return result
        finally:
            clear_correlation_id()

    return process_incident


def correlation_id_from(request: Any) -> str | None:
    """The correlation id the producer put in the message headers, if any."""
    value = getattr(request, CORRELATION_HEADER, None)
    if not value:
        headers = getattr(request, "headers", None) or {}
        value = headers.get(CORRELATION_HEADER)
    return str(value) if value else None


@worker_process_shutdown.connect
def _flush_traces(**_: Any) -> None:
    get_tracer().flush()


process_incident = build_incident_task(celery_app, get_settings())

__all__ = [
    "IncidentTask",
    "correlation_id_from",
    "build_incident_task",
    "invoke_graph",
    "process_incident",
    "record_dead_letter",
]
