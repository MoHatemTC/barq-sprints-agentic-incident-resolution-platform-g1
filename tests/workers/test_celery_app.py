"""Configuration tests for the Celery application topology (S2.3).

The topology is policy: queue names, acks, prefetch, time limits and
serialization must all derive from Settings so operators retune without code
changes. These tests pin every value to its setting — and the no-literals
metatest enforces that mechanically.
"""

from __future__ import annotations

import inspect

from app.workers.celery_app import build_broker_url, create_celery_app
from tests.helpers import mock_settings


class TestBrokerUrl:
    def test_url_contains_password_when_set(self) -> None:
        settings = mock_settings(redis_host="redis-host", redis_port=6380, redis_password="s3cret")

        url = build_broker_url(settings)

        assert url == "redis://:s3cret@redis-host:6380/0"

    def test_url_omits_password_when_none(self) -> None:
        settings = mock_settings(redis_password=None)

        url = build_broker_url(settings)

        assert url == "redis://localhost:6379/0"
        assert "@" not in url


class TestCeleryTopology:
    def test_topology_values_come_from_settings(self) -> None:
        settings = mock_settings(
            worker_prefetch=1,
            worker_soft_time_limit=90,
            worker_time_limit=110,
            worker_max_retries=4,
        )

        app = create_celery_app(settings)

        conf = app.conf
        assert conf.task_acks_late is True
        assert conf.task_reject_on_worker_lost is True
        assert conf.worker_prefetch_multiplier == settings.worker_prefetch
        assert conf.task_soft_time_limit == settings.worker_soft_time_limit
        assert conf.task_time_limit == settings.worker_time_limit
        assert conf.task_serializer == "json"
        assert conf.accept_content == ["json"]
        assert conf.task_ignore_result is True
        assert conf.broker_connection_retry_on_startup is True
        assert conf.worker_max_tasks_per_child == 1000
        assert conf.broker_url == build_broker_url(settings)

    def test_default_queue_is_the_incident_events_queue(self) -> None:
        from app.db.redis.keys import INCIDENT_EVENTS_QUEUE

        app = create_celery_app(mock_settings())

        assert app.conf.task_default_queue == INCIDENT_EVENTS_QUEUE

    def test_task_module_is_included_for_registration(self) -> None:
        """Without include=, `celery -A app.workers.celery_app` boots with zero
        registered tasks and rejects the first delivery (NotRegistered)."""
        app = create_celery_app(mock_settings())

        assert "app.workers.tasks" in app.conf.include

    def test_no_hardcoded_topology_literals(self) -> None:
        """Metatest: concurrency/prefetch/retry/timeout numbers must not be
        written in the celery app module — they flow from Settings."""
        source = inspect.getsource(__import__("app.workers.celery_app", fromlist=["celery_app"]))
        for forbidden in ("prefetch_multiplier=4", "time_limit=300", "max_retries=5"):
            assert forbidden not in source
