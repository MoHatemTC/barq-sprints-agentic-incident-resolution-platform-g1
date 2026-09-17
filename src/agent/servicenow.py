"""ServiceNow access for the graph: OAuth client (S1.5) behind a permitted-action list.

Manual §11.6 lists what the pilot may do; everything else "does not exist in the
service". :class:`IncidentGateway` is the only way a node reaches ServiceNow, and
it refuses any action not on :data:`PERMITTED_ACTIONS` at call time.

The S1.5 client is async and the Celery task is sync. The gateway owns one
background event loop per worker process, so the HTTP client and its OAuth token
cache survive across tasks instead of being rebuilt per incident.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Coroutine
from concurrent.futures import Future
from typing import Any, Protocol, TypeVar

from app.exceptions.servicenow import (
    ServiceNowAuthenticationError,
    ServiceNowAuthorizationError,
    ServiceNowConnectionError,
    ServiceNowError,
    ServiceNowHumanLockError,
    ServiceNowNotFoundError,
    ServiceNowRateLimitError,
    ServiceNowServerError,
    ServiceNowTimeoutError,
)
from app.models.incident import IncidentUpdatePayload
from app.workers.retry_policy import RetryableError, TerminalError
from observability.tracing import Tracer

T = TypeVar("T")

#: Manual §11.6 "Permitted actions" — name → risk class.
PERMITTED_ACTIONS: dict[str, str] = {
    "read_incident": "read",
    "search_knowledge": "read",
    "write_work_note": "low",
    "flag_human_review": "low",
    "write_ai_fields": "low",
}

_RETRYABLE = (
    ServiceNowConnectionError,
    ServiceNowTimeoutError,
    ServiceNowRateLimitError,
    ServiceNowServerError,
)
_TERMINAL = (
    ServiceNowAuthenticationError,
    ServiceNowAuthorizationError,
    ServiceNowNotFoundError,
)


class ActionNotPermittedError(TerminalError):
    """A node asked for an action outside the permitted list."""


class HumanLockedError(Exception):
    """The incident was locked by an analyst between read and write."""


class IncidentBackend(Protocol):
    """The subset of ``ServiceNowClient`` the gateway calls."""

    def get_incident(self, sys_id: str) -> Awaitable[Any]: ...

    def update_incident(self, sys_id: str, payload: IncidentUpdatePayload) -> Awaitable[Any]: ...

    def add_work_note(self, sys_id: str, note: str) -> Awaitable[Any]: ...


class AsyncRunner:
    """Runs coroutines on a private daemon event loop (one per process)."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="servicenow-loop", daemon=True
        )
        self._thread.start()

    def run(self, coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
        future: Future[T] = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


def translate_error(exc: BaseException) -> BaseException:
    """Map a ServiceNow client error onto the worker's retry taxonomy."""
    if isinstance(exc, ServiceNowHumanLockError):
        return HumanLockedError(str(exc))
    if isinstance(exc, (*_RETRYABLE, TimeoutError, ConnectionError)):
        return RetryableError(f"ServiceNow transient failure: {type(exc).__name__}")
    if isinstance(exc, _TERMINAL):
        return TerminalError(f"ServiceNow refused the request: {type(exc).__name__}")
    if isinstance(exc, ServiceNowError):
        return TerminalError(f"ServiceNow error: {type(exc).__name__}")
    return exc


class IncidentGateway:
    """Allow-listed, traced, sync access to the incident record."""

    def __init__(
        self,
        backend_factory: Callable[[], IncidentBackend],
        tracer: Tracer,
        *,
        runner: AsyncRunner | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._backend_factory = backend_factory
        self._backend: IncidentBackend | None = None
        self._tracer = tracer
        self._runner = runner
        self._timeout = timeout_seconds
        self.calls: list[str] = []

    def _get_backend(self) -> IncidentBackend:
        if self._backend is None:
            self._backend = self._backend_factory()
        return self._backend

    def _call(self, action: str, sys_id: str, make: Callable[[IncidentBackend], Awaitable[T]]) -> T:
        if action not in PERMITTED_ACTIONS:
            raise ActionNotPermittedError(f"action '{action}' is not permitted")
        self.calls.append(action)
        with self._tracer.span(
            f"servicenow.{action}",
            as_type="tool",
            metadata={"sys_id": sys_id, "risk": PERMITTED_ACTIONS[action]},
        ) as span:
            try:
                result = self._await(make(self._get_backend()))
            except Exception as exc:
                translated = translate_error(exc)
                span.update(metadata={"error_type": type(exc).__name__})
                if translated is exc:
                    raise
                raise translated from exc
            span.update(output={"ok": True})
            return result

    def _await(self, awaitable: Awaitable[T]) -> T:
        async def _wrap() -> T:
            return await awaitable

        if self._runner is None:
            self._runner = AsyncRunner()
        return self._runner.run(_wrap(), timeout=self._timeout)

    # -- permitted actions -------------------------------------------------------------

    def read_incident(self, sys_id: str) -> dict[str, Any]:
        incident = self._call("read_incident", sys_id, lambda b: b.get_incident(sys_id))
        return (
            incident.model_dump(mode="json") if hasattr(incident, "model_dump") else dict(incident)
        )

    def write_ai_fields(self, sys_id: str, payload: IncidentUpdatePayload) -> None:
        action = "flag_human_review" if _only_review_flag(payload) else "write_ai_fields"
        self._call(action, sys_id, lambda b: b.update_incident(sys_id, payload))

    def write_work_note(self, sys_id: str, note: str) -> None:
        self._call("write_work_note", sys_id, lambda b: b.add_work_note(sys_id, note))


def _only_review_flag(payload: IncidentUpdatePayload) -> bool:
    fields = payload.model_dump(exclude_none=True)
    return set(fields) == {"ai_human_review_required"}


def build_servicenow_backend() -> IncidentBackend:
    from app.clients.servicenow_client import ServiceNowClient
    from app.core.config import get_settings

    return ServiceNowClient(get_settings())


__all__ = [
    "PERMITTED_ACTIONS",
    "ActionNotPermittedError",
    "AsyncRunner",
    "HumanLockedError",
    "IncidentBackend",
    "IncidentGateway",
    "build_servicenow_backend",
    "translate_error",
]
