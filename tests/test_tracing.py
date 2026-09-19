"""Langfuse tracing tests (S2.5, FR-19).

The real Langfuse client runs with an in-memory OpenTelemetry exporter, so these
tests inspect exactly the spans that would be sent to Langfuse:

- **TestCorrelation** — webhook receipt, enqueue, worker pickup, every node and
  every ServiceNow/LLM call land in one trace, in execution order.
- **TestSecretScan** — no credential or personal data reaches exported spans or logs.
- **TestInducedFailure** — broken tracing never changes an execution's result.
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from contextlib import redirect_stdout
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent.graph import build_graph, run_graph
from agent.nodes import NODE_ORDER
from agent.state import EventPayload
from app.core.config import Environment
from app.core.logging import configure_logging
from app.repositories.idempotency import EventAcceptanceResult, EventAcceptanceStatus
from observability import tracing as tracing_module
from observability.redaction import langfuse_mask, redact_text, redact_value
from observability.tracing import (
    NOOP_SPAN,
    Tracer,
    TracingSettings,
    build_tracer,
    trace_id_for,
)
from tests.agent_support import (
    EXECUTION_ID,
    VPN,
    FakeLLM,
    FakeServiceNow,
    event_for,
    make_deps,
    sdk_llm,
    vpn_answers,
)

SECRET_TEXTS = {
    "password": "Hunter2-Winter!",
    "bearer": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZG1pbiJ9.c2lnbmF0dXJlLXZhbHVl",
    "litellm_key": "sk-FAKElitellmKEY00000000",
    "email": "mariam.fouad@barq.example",
    "phone": "+971 50 123 4567",
    "langfuse_secret": "sk-lf-00000000-1111-2222-3333-444444444444",
}


def recording_tracer(**overrides: Any) -> tuple[Tracer, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    settings = TracingSettings(
        _env_file=None,
        tracing_enabled=True,
        langfuse_public_key=f"pk-lf-test-{uuid.uuid4()}",
        langfuse_secret_key=SECRET_TEXTS["langfuse_secret"],
        langfuse_base_url="http://127.0.0.1:9",
    )
    tracer = build_tracer(
        settings,
        span_exporter=overrides.pop("span_exporter", exporter),
        tracer_provider=TracerProvider(),
        **overrides,
    )
    assert tracer.enabled
    return tracer, exporter


def finished(tracer: Tracer, exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    tracer.flush()
    return sorted(exporter.get_finished_spans(), key=lambda s: s.start_time or 0)


def everything_exported(spans: list[ReadableSpan]) -> str:
    parts = []
    for span in spans:
        parts.append(span.name)
        for value in (span.attributes or {}).values():
            parts.append(str(value))
        for event in span.events:
            parts.append(json.dumps(dict(event.attributes or {}), default=str))
    return "\n".join(parts)


def run_vpn(tracer: Tracer, record: dict[str, Any] = VPN, llm: Any = None) -> dict[str, Any]:
    backend = FakeServiceNow({record["number"]: record})
    deps = make_deps(tracer=tracer, servicenow=backend, llm=llm or sdk_llm(tracer))
    graph = build_graph(deps)
    with (
        tracer.span("worker.pickup", correlation_id="corr-e2e", as_type="agent"),
        tracer.trace_attributes(
            correlation_id="corr-e2e",
            incident_number=record["number"],
            execution_id=EXECUTION_ID,
        ),
    ):
        return run_graph(
            graph,
            EventPayload.model_validate(event_for(record)),
            execution_id=EXECUTION_ID,
            correlation_id="corr-e2e",
            attempt=1,
            deps=deps,
        )


@pytest.fixture(autouse=True)
def _isolate_global_tracer():
    tracing_module.get_tracer.cache_clear()
    yield
    tracing_module.get_tracer.cache_clear()


# -- correlation --------------------------------------------------------------------------


class TestCorrelation:
    def test_trace_id_is_deterministic_per_correlation_id(self) -> None:
        assert trace_id_for("abc") == trace_id_for("abc")
        assert trace_id_for("abc") != trace_id_for("abd")
        assert len(trace_id_for("abc")) == 32

    def test_one_trace_with_ordered_per_node_spans(self) -> None:
        tracer, exporter = recording_tracer()
        result = run_vpn(tracer)
        spans = finished(tracer, exporter)
        assert result["outcome"] == "suggested"

        trace_ids = {format(s.context.trace_id, "032x") for s in spans}
        assert trace_ids == {trace_id_for("corr-e2e")}

        node_spans = [s.name for s in spans if s.name.startswith("node.")]
        assert node_spans == [f"node.{n}" for n in NODE_ORDER]

        names = [s.name for s in spans]
        for expected in (
            "worker.pickup",
            "servicenow.read_incident",
            "llm.classify",
            "llm.diagnose",
            "llm.generate",
            "servicenow.write_ai_fields",
        ):
            assert expected in names

        root = next(s for s in spans if s.name == "worker.pickup")
        for span in spans:
            if span is not root:
                assert span.parent is not None

        # Each child is inside its node: llm.classify's parent is node.classify.
        by_id = {s.context.span_id: s for s in spans}
        classify_call = next(s for s in spans if s.name == "llm.classify")
        assert by_id[classify_call.parent.span_id].name == "node.classify"

    def test_trace_is_keyed_by_incident_number_and_execution_id(self) -> None:
        tracer, exporter = recording_tracer()
        run_vpn(tracer)
        spans = finished(tracer, exporter)
        attributes = {k: v for s in spans for k, v in (s.attributes or {}).items()}
        assert attributes.get("session.id") == "INC0010023"
        dumped = everything_exported(spans)
        assert EXECUTION_ID in dumped
        assert "incident-execution" in dumped

    def test_generation_records_model_usage_cost_and_prompt_version(self) -> None:
        from agent.prompts import ClassifyOutput

        tracer, exporter = recording_tracer()
        llm = sdk_llm(tracer)
        with tracer.span("worker.pickup", correlation_id="corr-gen"):
            llm.structured(purpose="classify", system="s", prompt="p", schema=ClassifyOutput)
        span = next(s for s in finished(tracer, exporter) if s.name == "llm.classify")
        attrs = span.attributes or {}
        assert attrs["langfuse.observation.type"] == "generation"
        assert attrs["langfuse.observation.model.name"] == "gemini/gemini-3.5-flash"
        usage = json.loads(str(attrs["langfuse.observation.usage_details"]))
        assert usage == {"input": 900, "output": 150, "reasoning_tokens": 40}
        cost = json.loads(str(attrs["langfuse.observation.cost_details"]))
        assert cost == {"total": pytest.approx(0.0016455)}
        assert attrs["langfuse.version"] == "v1"
        assert "prompt_version" in str(attrs)

    @pytest.mark.asyncio
    async def test_webhook_and_worker_share_one_trace(self) -> None:
        """Receipt and enqueue (API process) + pickup (worker) → one trace id."""
        from app.main import create_app
        from app.workers.producer import CORRELATION_HEADER
        from tests.helpers import mock_settings

        tracer, exporter = recording_tracer()
        app = create_app(settings=mock_settings(webhook_auth_token="tok"))
        app.state.engine = MagicMock()
        app.state.session_factory = MagicMock()
        app.state.redis = MagicMock()
        accepted = EventAcceptanceResult(
            status=EventAcceptanceStatus.ACCEPTED,
            event_id="evt-1",
            event_record_id=uuid4(),
            execution_id=uuid4(),
        )
        payload = {
            "event_id": "evt-1",
            "sys_id": VPN["sys_id"],
            "number": VPN["number"],
            "event_type": "incident.created",
            "contract_version": "v1",
        }
        with (
            patch("api.routers.webhook.get_tracer", return_value=tracer),
            patch("api.routers.webhook.accept_inbound_event", new_callable=AsyncMock) as accept,
            patch("app.workers.producer.celery_app.send_task") as send_task,
        ):
            accept.return_value = accepted
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/v1/webhook/incident",
                    json=payload,
                    headers={"Authorization": "Bearer tok", "X-Correlation-ID": "corr-http-1"},
                )
        assert resp.status_code == 202
        assert resp.json()["correlation_id"] == "corr-http-1"
        headers = send_task.call_args.kwargs["headers"]
        assert headers == {CORRELATION_HEADER: "corr-http-1"}

        # The worker side, with only the header to go on.
        from app.workers.tasks import correlation_id_from

        request = MagicMock(spec=["headers"])
        request.headers = headers
        worker_cid = correlation_id_from(request)
        with tracer.span("worker.pickup", correlation_id=worker_cid):
            pass

        spans = finished(tracer, exporter)
        names = [s.name for s in spans]
        assert names == ["webhook.receipt", "queue.enqueue", "worker.pickup"]
        by_id = {s.context.span_id: s for s in spans}
        enqueue = spans[1]
        assert enqueue.parent is not None
        assert by_id[enqueue.parent.span_id].name == "webhook.receipt"
        assert {format(s.context.trace_id, "032x") for s in spans} == {trace_id_for("corr-http-1")}

    @pytest.mark.asyncio
    async def test_unauthenticated_webhook_calls_are_not_traced(self) -> None:
        from app.main import create_app
        from tests.helpers import mock_settings

        tracer, exporter = recording_tracer()
        app = create_app(settings=mock_settings(webhook_auth_token="tok"))
        app.state.engine = MagicMock()
        app.state.session_factory = MagicMock()
        app.state.redis = MagicMock()
        payload = {
            "event_id": "evt-x",
            "sys_id": VPN["sys_id"],
            "number": VPN["number"],
            "event_type": "incident.created",
        }
        with (
            patch("api.routers.webhook.get_tracer", return_value=tracer),
            patch("api.routers.webhook.accept_inbound_event", new_callable=AsyncMock) as accept,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                missing = await client.post("/api/v1/webhook/incident", json=payload)
                wrong = await client.post(
                    "/api/v1/webhook/incident",
                    json=payload,
                    headers={"Authorization": "Bearer nope"},
                )
        assert (missing.status_code, wrong.status_code) == (401, 401)
        accept.assert_not_called()
        assert finished(tracer, exporter) == []

    def test_celery_request_exposes_the_header(self) -> None:
        from app.workers.tasks import correlation_id_from

        as_attribute = MagicMock(spec=["x_correlation_id", "headers"])
        as_attribute.x_correlation_id = "cid-attr"
        as_attribute.headers = None
        assert correlation_id_from(as_attribute) == "cid-attr"

        missing = MagicMock(spec=["headers"])
        missing.headers = None
        assert correlation_id_from(missing) is None


# -- secret scan ---------------------------------------------------------------------------


class TestSecretScan:
    def _leaky_incident(self) -> dict[str, Any]:
        return {
            **VPN,
            "description": (
                f"VPN says invalid credentials. My password is {SECRET_TEXTS['password']}. "
                f"Header was Authorization: Bearer {SECRET_TEXTS['bearer']}. "
                f"Key {SECRET_TEXTS['litellm_key']}. Reach me at {SECRET_TEXTS['email']} "
                f"or {SECRET_TEXTS['phone']}."
            ),
        }

    def test_no_secret_or_personal_data_in_exported_spans(self) -> None:
        tracer, exporter = recording_tracer()
        result = run_vpn(tracer, record=self._leaky_incident())
        assert result["outcome"] == "suggested"
        dumped = everything_exported(finished(tracer, exporter))

        assert "***REDACTED***" in dumped  # the scan saw the incident text
        for kind, secret in SECRET_TEXTS.items():
            assert secret not in dumped, f"{kind} leaked into a span"

    def test_no_secret_in_a_failed_span_status_message(self) -> None:
        """``status_message`` is outside the mask hook, so ``fail`` must redact it.

        A pydantic ValidationError quotes the values it rejected, so an exception
        message is a real export path for credentials and personal data.
        """
        tracer, exporter = recording_tracer()
        leaky = (
            f"rejected record: password={SECRET_TEXTS['password']} "
            f"token={SECRET_TEXTS['bearer']} key={SECRET_TEXTS['litellm_key']} "
            f"contact={SECRET_TEXTS['email']} / {SECRET_TEXTS['phone']}"
        )
        with tracer.span("node.generate") as span:
            span.fail(ValueError(leaky))

        dumped = everything_exported(finished(tracer, exporter))
        for kind, secret in SECRET_TEXTS.items():
            assert secret not in dumped, f"{kind} leaked via status_message"

    def test_no_secret_reaches_the_model_prompt(self) -> None:
        tracer, _ = recording_tracer()
        llm = FakeLLM(vpn_answers())
        run_vpn(tracer, record=self._leaky_incident(), llm=llm)
        prompts = "\n".join(c["prompt"] for c in llm.calls)
        for kind, secret in SECRET_TEXTS.items():
            assert secret not in prompts, f"{kind} reached the model"

    def test_no_secret_in_logs(self) -> None:
        configure_logging(environment=Environment.PRODUCTION, log_level="DEBUG")
        try:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                logger = structlog.get_logger("scan")
                tracer, exporter = recording_tracer()
                run_vpn(tracer, record=self._leaky_incident())
                # A tracing failure logs — make sure that path is covered too.
                broken = Tracer(
                    MagicMock(
                        **{
                            "start_as_current_observation.side_effect": ValueError(
                                SECRET_TEXTS["password"]
                            )
                        }
                    )
                )
                with broken.span("x", input=self._leaky_incident()):
                    pass
                logger.info("probe", client_secret=SECRET_TEXTS["password"])
            output = buffer.getvalue()
        finally:
            structlog.reset_defaults()
            logging.getLogger().handlers.clear()
        assert "tracing_degraded" in output
        for kind, secret in SECRET_TEXTS.items():
            assert secret not in output, f"{kind} leaked into logs"

    def test_settings_secrets_never_appear(self) -> None:
        tracer, exporter = recording_tracer()
        run_vpn(tracer)
        dumped = everything_exported(finished(tracer, exporter))
        assert SECRET_TEXTS["langfuse_secret"] not in dumped
        assert "test-client-secret" not in dumped
        assert "test-password" not in dumped

    @pytest.mark.parametrize(
        "text",
        [
            "Authorization: Basic dXNlcjpwYXNzd29yZA==",
            "client_secret: abcdEFGH1234",
            "https://svc:Sup3rS3cret@dev407364.service-now.com",
            "token=ghp_abcdefghijklmnopqrstuvwxyz0123",
            "AKIAABCDEFGHIJKLMNOP",
            "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----",
            "call 0501234567",
        ],
    )
    def test_credential_shapes_are_redacted(self, text: str) -> None:
        redacted = redact_text(text)
        assert redacted != text
        for fragment in (
            "dXNlcjpwYXNzd29yZA",
            "abcdEFGH1234",
            "Sup3rS3cret",
            "ghp_abc",
            "AKIAABCD",
            "MIIE",
            "0501234567",
        ):
            assert fragment not in redacted

    def test_identifiers_survive_redaction(self) -> None:
        text = (
            "INC0010052 sys_id a1b2c3d4e5f60718293a4b5c6d7e8f90 "
            "at 2026-09-08T15:47:00Z P1 KB0010 v2"
        )
        assert redact_text(text) == text

    def test_mask_is_key_aware_and_never_raises(self) -> None:
        assert redact_value({"Authorization": "x", "nested": [{"api_key": "y"}]}) == {
            "Authorization": "***REDACTED***",
            "nested": [{"api_key": "***REDACTED***"}],
        }

        with patch("observability.redaction.redact_value", side_effect=RuntimeError):
            assert langfuse_mask(data={"a": 1}) == "***REDACTED***"


# -- induced failure ---------------------------------------------------------------------


class _FailingExporter(SpanExporter):
    def export(self, spans: Any) -> SpanExportResult:
        raise ConnectionError("langfuse unreachable")

    def shutdown(self) -> None:
        return None


class TestInducedFailure:
    def baseline(self) -> dict[str, Any]:
        return run_vpn(Tracer(None))

    def test_span_creation_failure_does_not_change_the_result(self) -> None:
        client = MagicMock()
        client.start_as_current_observation.side_effect = RuntimeError("otel broke")
        tracer = Tracer(client)
        assert run_vpn(tracer) == self.baseline()
        assert tracer.failures["span_start_failed"] >= len(NODE_ORDER)

    def test_span_update_and_close_failures_do_not_change_the_result(self) -> None:
        observation = MagicMock()
        observation.update.side_effect = RuntimeError("update broke")
        cm = MagicMock()
        cm.__enter__.return_value = observation
        cm.__exit__.side_effect = RuntimeError("close broke")
        client = MagicMock()
        client.start_as_current_observation.return_value = cm
        tracer = Tracer(client)
        assert run_vpn(tracer) == self.baseline()
        assert tracer.failures["span_update_failed"] > 0
        assert tracer.failures["span_end_failed"] > 0

    def test_exporter_failure_does_not_change_the_result(self) -> None:
        tracer, _ = recording_tracer(span_exporter=_FailingExporter())
        assert run_vpn(tracer) == self.baseline()
        tracer.flush()  # must not raise

    def test_unreachable_langfuse_does_not_change_the_result(self) -> None:
        # Real OTLP exporter pointed at a closed port.
        settings = TracingSettings(
            _env_file=None,
            tracing_enabled=True,
            langfuse_public_key=f"pk-lf-test-{uuid.uuid4()}",
            langfuse_secret_key="sk-lf-x",
            langfuse_base_url="http://127.0.0.1:9",
        )
        tracer = build_tracer(settings, tracer_provider=TracerProvider(), timeout=1)
        assert tracer.enabled
        assert run_vpn(tracer) == self.baseline()
        tracer.flush()
        tracer.shutdown()

    def test_client_construction_failure_disables_tracing(self) -> None:
        settings = TracingSettings(
            _env_file=None, tracing_enabled=True, langfuse_public_key="pk", langfuse_secret_key="sk"
        )
        with patch("langfuse.Langfuse", side_effect=RuntimeError("bad config")):
            tracer = build_tracer(settings)
        assert tracer.enabled is False
        assert run_vpn(tracer) == self.baseline()

    def test_propagation_failure_is_swallowed(self) -> None:
        tracer, _ = recording_tracer()
        with patch("langfuse.propagate_attributes", side_effect=RuntimeError("baggage")):
            with tracer.trace_attributes(correlation_id="c", incident_number="INC1"):
                pass
        assert tracer.failures["propagate_failed"] == 1

    def test_errors_from_traced_code_propagate_unchanged(self) -> None:
        tracer, exporter = recording_tracer()
        error = ValueError("the node's own failure")
        with pytest.raises(ValueError) as raised:
            with tracer.span("node.x", correlation_id="c"):
                raise error
        assert raised.value is error
        span = finished(tracer, exporter)[0]
        assert (span.attributes or {}).get("langfuse.observation.level") == "ERROR"

    def test_missing_keys_mean_tracing_off(self) -> None:
        assert build_tracer(TracingSettings(_env_file=None, tracing_enabled=True)).enabled is False
        assert (
            build_tracer(
                TracingSettings(
                    _env_file=None,
                    tracing_enabled=False,
                    langfuse_public_key="p",
                    langfuse_secret_key="s",
                )
            ).enabled
            is False
        )

    def test_noop_tracer_is_inert(self) -> None:
        tracer = Tracer(None)
        with tracer.span("x", correlation_id="c") as span:
            span.update(output={"a": 1})
        assert span is NOOP_SPAN
        assert tracer.current_trace_id() is None
        assert tracer.trace_url("c") is None
        tracer.flush()
        tracer.shutdown()
