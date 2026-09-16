"""Celery application for the incident-processing substrate (S2.3).

Topology decisions (rationale in docs/sprint2_worker_topology.md):

- **acks_late + reject_on_worker_lost**: a task is acknowledged only after it
  finishes; a worker that is killed mid-task (SIGKILL, OOM) rejects instead of
  acking, so the message is redelivered. Combined with the database idempotency
  gate this yields exactly-once *effect* from at-least-once delivery.
- **prefetch = 1**: graph runs are long; prefetching would hoard jobs behind
  an idle-but-loaded worker.
- **JSON only**: task payloads never use pickle (remote code execution risk if
  a broker is compromised).
- **Everything from Settings**: no concurrency/retry/timeout literals here.
"""

from __future__ import annotations

from celery import Celery
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.db.redis.keys import INCIDENT_EVENTS_QUEUE


def build_broker_url(settings: Settings) -> str:
    """Redis broker URL honoring the optional password, credentials escaped."""
    password = (
        settings.redis_password.get_secret_value()
        if isinstance(settings.redis_password, SecretStr) and settings.redis_password is not None
        else None
    )
    if password:
        return f"redis://:{password}@{settings.redis_host}:{settings.redis_port}/0"
    return f"redis://{settings.redis_host}:{settings.redis_port}/0"


def create_celery_app(settings: Settings) -> Celery:
    """Build the worker application; every topology value comes from settings."""
    app = Celery(
        "barq_workers",
        # include= is mandatory: `celery -A app.workers.celery_app` imports only
        # this module; without it the worker boots with zero registered tasks.
        include=["app.workers.tasks"],
        broker=build_broker_url(settings),
    )
    app.conf.update(
        task_default_queue=INCIDENT_EVENTS_QUEUE,
        # Reliability: no silent losses.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        # Long-running graph tasks: never hoard jobs.
        worker_prefetch_multiplier=settings.worker_prefetch,
        # Applied here so a bare `celery worker` (no --concurrency flag) does
        # not silently spawn CPU-count children; a CLI flag still overrides.
        worker_concurrency=settings.worker_concurrency,
        # Fault containment: hung jobs cannot hold workers indefinitely.
        task_soft_time_limit=settings.worker_soft_time_limit,
        task_time_limit=settings.worker_time_limit,
        # Security: pickle deserialization is code execution if a broker leaks.
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # Results live in PostgreSQL, not Redis.
        task_ignore_result=True,
        # Survive Redis restarting first in the Compose stack.
        broker_connection_retry_on_startup=True,
        # Recycle prefork children to bound long-running memory growth.
        worker_max_tasks_per_child=1000,
    )
    return app


celery_app = create_celery_app(get_settings())

__all__ = ["build_broker_url", "celery_app", "create_celery_app"]
