"""Langfuse tracing for the incident pipeline (S2.5, FR-19).

One correlation identifier travels webhook → queue → Celery worker. The Langfuse
trace id is derived from it deterministically (``Langfuse.create_trace_id(seed=…)``),
so spans emitted in different processes land in one trace without anything but the
correlation id crossing the queue. Every span carries the execution id and the
incident number, and the trace's ``session_id`` is the incident number, so a trace
can be found from either key.

Span map (all children of one trace, in this order):

====================  ==============================================================
``webhook.receipt``   API process — authentication, validation, idempotent persist
``queue.enqueue``     API process — publish to the Celery broker
``worker.pickup``     worker process — root of the execution; attempt number attached
``node.<name>``       one per LangGraph node, in execution order
``servicenow.<op>``   every ServiceNow call made by a node
``llm.<purpose>``     every model call (generation: model, tokens, cost, prompt version)
====================  ==============================================================

**Tracing never breaks an execution.** Every call into Langfuse is wrapped: a failure
to create, update or close a span is logged once and replaced by a no-op span, while
exceptions raised by the traced code itself always propagate unchanged
(``tests/test_tracing.py::TestInducedFailure``).

**Nothing secret reaches Langfuse.** The client is created with
:func:`observability.redaction.langfuse_mask`, which redacts every input, output and
metadata value before export (``tests/test_tracing.py::TestSecretScan``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal

import structlog
from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from observability.redaction import langfuse_mask

if TYPE_CHECKING:
    from langfuse import Langfuse

logger = structlog.getLogger(__name__)

SpanType = Literal["span", "generation", "agent", "tool", "chain", "retriever", "guardrail"]
SpanLevel = Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"]

TRACE_NAME = "incident-execution"


class TracingSettings(BaseSettings):
    """Langfuse configuration. Keys are read from the standard Langfuse variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    tracing_enabled: bool = Field(
        default=True,
        description="Master switch. Tracing is also off when the Langfuse keys are unset.",
    )
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LANGFUSE_BASE_URL", "LANGFUSE_HOST"),
        description="Langfuse API URL, e.g. https://cloud.langfuse.com or a self-hosted URL",
    )
    langfuse_environment: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LANGFUSE_TRACING_ENVIRONMENT", "ENVIRONMENT"),
    )
    langfuse_release: str | None = Field(
        default=None, validation_alias=AliasChoices("LANGFUSE_RELEASE", "AGENT_VERSION")
    )
    tracing_sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)

    @property
    def configured(self) -> bool:
        return bool(
            self.tracing_enabled
            and self.langfuse_public_key
            and self.langfuse_secret_key is not None
            and self.langfuse_secret_key.get_secret_value()
        )


def trace_id_for(correlation_id: str) -> str:
    """Deterministic 32-hex Langfuse trace id for a correlation id."""
    try:
        from langfuse import Langfuse

        return Langfuse.create_trace_id(seed=correlation_id)
    except Exception:  # noqa: BLE001 — same shape as Langfuse's own derivation
        return hashlib.sha256(correlation_id.encode("utf-8")).hexdigest()[:32]


def _span_active() -> bool:
    """Whether an OpenTelemetry span is current (Langfuse's own getter logs when not)."""
    from opentelemetry import trace

    return trace.get_current_span().get_span_context().is_valid


def _safe_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if metadata is None:
        return None
    return {str(k): v for k, v in metadata.items() if v is not None}


class Span:
    """Handle to one observation. Every method is safe to call on a dead span."""

    __slots__ = ("_observation", "_tracer")

    def __init__(self, observation: Any = None, tracer: Tracer | None = None) -> None:
        self._observation = observation
        self._tracer = tracer

    @property
    def recording(self) -> bool:
        return self._observation is not None

    def update(self, **fields: Any) -> None:
        if self._observation is None:
            return
        if "metadata" in fields:
            fields["metadata"] = _safe_metadata(fields["metadata"])
        try:
            self._observation.update(**fields)
        except Exception as exc:  # noqa: BLE001
            _report(self._tracer, "span_update_failed", exc)

    def fail(self, exc: BaseException) -> None:
        self.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}"[:500])


NOOP_SPAN = Span()


def _report(tracer: Tracer | None, event: str, exc: BaseException) -> None:
    """Log a tracing failure, once per event kind, without the exception payload
    (which could echo request data)."""
    if tracer is not None:
        if event in tracer.failures:
            tracer.failures[event] += 1
            return
        tracer.failures[event] = 1
    logger.warning("tracing_degraded", tracing_event=event, error_type=type(exc).__name__)


class Tracer:
    """Fail-safe facade over the Langfuse client (``None`` client = tracing off)."""

    def __init__(self, client: Langfuse | None) -> None:
        self._client = client
        #: Count of swallowed tracing failures by kind — asserted by the induced
        #: failure tests and useful in a debugger.
        self.failures: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @property
    def client(self) -> Langfuse | None:
        return self._client

    # -- spans -------------------------------------------------------------------

    @contextmanager
    def span(
        self,
        name: str,
        *,
        correlation_id: str | None = None,
        as_type: SpanType = "span",
        input: Any = None,
        metadata: Mapping[str, Any] | None = None,
        version: str | None = None,
        model: str | None = None,
        model_parameters: Mapping[str, Any] | None = None,
    ) -> Iterator[Span]:
        """Open a child of the current span, or a root span in the trace derived
        from ``correlation_id`` when no span is active."""
        cm: AbstractContextManager[Any] | None = None
        handle = NOOP_SPAN
        if self._client is not None:
            try:
                kwargs: dict[str, Any] = {
                    "name": name,
                    "as_type": as_type,
                    "input": input,
                    "metadata": _safe_metadata(metadata),
                    "version": version,
                }
                if as_type == "generation":
                    kwargs["model"] = model
                    kwargs["model_parameters"] = (
                        dict(model_parameters) if model_parameters else None
                    )
                if correlation_id and not _span_active():
                    kwargs["trace_context"] = {"trace_id": trace_id_for(correlation_id)}
                cm = self._client.start_as_current_observation(**kwargs)
                handle = Span(cm.__enter__(), self)
            except Exception as exc:  # noqa: BLE001
                _report(self, "span_start_failed", exc)
                cm, handle = None, NOOP_SPAN

        try:
            yield handle
        except BaseException as exc:
            handle.fail(exc)
            self._close(cm, exc)
            raise
        else:
            self._close(cm, None)

    def _close(self, cm: AbstractContextManager[Any] | None, exc: BaseException | None) -> None:
        if cm is None:
            return
        try:
            if exc is None:
                cm.__exit__(None, None, None)
            else:
                cm.__exit__(type(exc), exc, exc.__traceback__)
        except Exception as close_exc:  # noqa: BLE001
            _report(self, "span_end_failed", close_exc)

    @contextmanager
    def trace_attributes(
        self,
        *,
        correlation_id: str,
        incident_number: str | None,
        execution_id: str | None = None,
        extra: Mapping[str, str] | None = None,
    ) -> Iterator[None]:
        """Stamp trace-level keys (session = incident number) on the current span and
        every span opened inside the block. Open it inside the root span."""
        cm: AbstractContextManager[Any] = nullcontext()
        if self._client is not None:
            try:
                from langfuse import propagate_attributes

                metadata = {
                    "correlation_id": correlation_id,
                    "incident_number": incident_number,
                    "execution_id": execution_id,
                    **(extra or {}),
                }
                tags = [f"incident:{incident_number}"] if incident_number else []
                if execution_id:
                    tags.append(f"execution:{execution_id}")
                cm = propagate_attributes(
                    session_id=incident_number,
                    trace_name=TRACE_NAME,
                    metadata={k: str(v) for k, v in metadata.items() if v is not None},
                    tags=tags,
                )
                cm.__enter__()
            except Exception as exc:  # noqa: BLE001
                _report(self, "propagate_failed", exc)
                cm = nullcontext()
        try:
            yield
        finally:
            try:
                cm.__exit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                _report(self, "propagate_end_failed", exc)

    # -- lifecycle ---------------------------------------------------------------

    def current_trace_id(self) -> str | None:
        if self._client is None or not _span_active():
            return None
        try:
            return self._client.get_current_trace_id()
        except Exception as exc:  # noqa: BLE001
            _report(self, "trace_id_failed", exc)
            return None

    def trace_url(self, correlation_id: str) -> str | None:
        if self._client is None:
            return None
        try:
            return self._client.get_trace_url(trace_id=trace_id_for(correlation_id))
        except Exception as exc:  # noqa: BLE001
            _report(self, "trace_url_failed", exc)
            return None

    def flush(self) -> None:
        if self._client is None:
            return
        try:
            self._client.flush()
        except Exception as exc:  # noqa: BLE001
            _report(self, "flush_failed", exc)

    def shutdown(self) -> None:
        if self._client is None:
            return
        try:
            self._client.shutdown()
        except Exception as exc:  # noqa: BLE001
            _report(self, "shutdown_failed", exc)


def build_tracer(settings: TracingSettings | None = None, **client_overrides: Any) -> Tracer:
    """Create the process tracer. Missing keys or any client error → tracing off."""
    settings = settings or TracingSettings()
    if not settings.configured:
        logger.info("tracing_disabled", reason="not configured")
        return Tracer(None)
    try:
        from langfuse import Langfuse

        assert settings.langfuse_secret_key is not None  # narrowed by .configured
        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            base_url=settings.langfuse_base_url,
            environment=settings.langfuse_environment,
            release=settings.langfuse_release,
            sample_rate=settings.tracing_sample_rate,
            mask=langfuse_mask,
            **client_overrides,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "tracing_disabled", reason="client init failed", error_type=type(exc).__name__
        )
        return Tracer(None)
    return Tracer(client)


@lru_cache
def get_tracer() -> Tracer:
    """Process-wide tracer (dependency-injection provider)."""
    return build_tracer()


def reset_tracer() -> None:
    """Drop the cached tracer (tests, and after reconfiguration)."""
    if get_tracer.cache_info().currsize:
        get_tracer().shutdown()
    get_tracer.cache_clear()


__all__ = [
    "NOOP_SPAN",
    "TRACE_NAME",
    "Span",
    "Tracer",
    "TracingSettings",
    "build_tracer",
    "get_tracer",
    "reset_tracer",
    "trace_id_for",
]
