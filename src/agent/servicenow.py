"""Synchronous ServiceNow transport adapter over the OAuth client (S1.5).

Manual §11.6 lists what the pilot may do; everything else "does not exist in the
service". The ToolRegistry owns application allowlisting and permission policy;
this module owns transport adaptation, tracing, and error translation.

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

from agent.errors import HumanLockedError
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
from app.models.execution_log import ExecutionLogCreatePayload
from app.models.incident import IncidentUpdatePayload
from app.workers.retry_policy import RetryableError, TerminalError
from observability.tracing import Tracer

T = TypeVar("T")

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


class AsyncCancellationError(RuntimeError):
    """A timed-out ServiceNow coroutine did not acknowledge cancellation."""


class IncidentBackend(Protocol):
    """The subset of ``ServiceNowClient`` the gateway calls."""

    def get_incident(self, sys_id: str) -> Awaitable[Any]: ...

    def update_incident(self, sys_id: str, payload: IncidentUpdatePayload) -> Awaitable[Any]: ...

    def add_work_note(self, sys_id: str, note: str) -> Awaitable[Any]: ...

    def write_execution_log(self, payload: ExecutionLogCreatePayload) -> Awaitable[Any]: ...


class AsyncRunner:
    """Runs coroutines on a private daemon event loop (one per process)."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="servicenow-loop", daemon=True
        )
        self._thread.start()

    def run(
        self,
        coro: Coroutine[Any, Any, T],
        timeout: float | None = None,
        *,
        cancellation_timeout: float = 5.0,
    ) -> T:
        completed = threading.Event()

        async def tracked() -> T:
            try:
                return await coro
            finally:
                completed.set()

        future: Future[T] = asyncio.run_coroutine_threadsafe(tracked(), self._loop)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            if not completed.wait(timeout=cancellation_timeout):
                raise AsyncCancellationError(
                    "Timed-out ServiceNow request did not stop; refusing a concurrent retry"
                ) from None
            raise

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
    """Traced, synchronous adaptation of the asynchronous incident client."""

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

    def _call(
        self,
        action: str,
        sys_id: str,
        risk: str,
        make: Callable[[IncidentBackend], Awaitable[T]],
    ) -> T:
        self.calls.append(action)
        with self._tracer.span(
            f"servicenow.{action}",
            as_type="tool",
            metadata={"sys_id": sys_id, "risk": risk},
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

    # -- transport operations ----------------------------------------------------------

    def read_incident(self, sys_id: str) -> dict[str, Any]:
        incident = self._call("read_incident", sys_id, "read", lambda b: b.get_incident(sys_id))
        return (
            incident.model_dump(mode="json") if hasattr(incident, "model_dump") else dict(incident)
        )

    def write_ai_fields(self, sys_id: str, payload: IncidentUpdatePayload) -> None:
        action = "flag_human_review" if _only_review_flag(payload) else "write_ai_fields"
        self._call(action, sys_id, "low", lambda b: b.update_incident(sys_id, payload))

    def write_work_note(self, sys_id: str, note: str) -> None:
        self._call("write_work_note", sys_id, "low", lambda b: b.add_work_note(sys_id, note))

    def write_execution_log(self, sys_id: str, payload: ExecutionLogCreatePayload) -> None:
        self._call("write_execution_log", sys_id, "low", lambda b: b.write_execution_log(payload))


def _only_review_flag(payload: IncidentUpdatePayload) -> bool:
    fields = payload.model_dump(exclude_none=True)
    return set(fields) == {"ai_human_review_required"}


def build_servicenow_backend() -> IncidentBackend:
    from app.clients.servicenow_client import ServiceNowClient
    from app.core.config import get_settings

    return ServiceNowClient(get_settings())


__all__ = [
    "AsyncCancellationError",
    "AsyncRunner",
    "HumanLockedError",
    "IncidentBackend",
    "IncidentGateway",
    "build_servicenow_backend",
    "translate_error",
]
