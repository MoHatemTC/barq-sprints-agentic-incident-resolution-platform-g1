"""S3.5 commit 4: the knowledge-capture orchestrator.

`capture_human_resolution` is the resume-path pipeline: compose (one LLM call,
sync client wrapped in to_thread) → publish through the registry (refusal ⇒ no
Qdrant write) → ingest via the existing S2.4 pipeline (deterministic IDs) →
audit through the registered execution-log tool. A late ingest failure is
*drift*, recorded and recoverable — never a crash, never a silent gap.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from structlog.testing import capture_logs

from agent.knowledge_capture import (
    capture_human_resolution,
    make_next_article_number,
)
from agent.prompts import ComposedArticle
from agent.state import IncidentSnapshot
from agent.tools.permissions import PermissionClass
from agent.tools.registry import RegistryRefusalError, RefusalReason
from app.models.execution_log import ExecutionAction, ExecutionStatus
from app.models.knowledge import WorkflowState
from app.workers.retry_policy import RetryableError, TerminalError
from tests.agent_support import FakeLLM, make_deps

EXECUTION_ID = "22222222-2222-4222-8222-222222222222"

INCIDENT = IncidentSnapshot(
    sys_id="inc-sys-1",
    number="INC0010099",
    short_description="VPN drops every few minutes",
    description="Corporate VPN drops intermittently on the requester laptop.",
    category="software",
    service="corporate-vpn",
)

SOLUTION = "was the MTU mismatch; fixed by setting MTU 1400 and restarting the tunnel interface"

COMPOSER_ANSWER = ComposedArticle(
    title="Resolving Intermittent Corporate VPN Drops",
    short_description="Set MTU 1400 and restart the tunnel interface.",
    category="network",
    body="1. Set MTU 1400 on the tunnel.\n2. Fixed by restarting the interface.",
)


class FakeKBClient:
    async def find_source_ids_by_prefix(
        self, prefix: str, *, kb_sys_id: str | None = None
    ) -> list[str]:
        return []


class ScriptedRegistry:
    """Records tool calls into a shared event list; scripts publish behavior."""

    def __init__(
        self,
        events: list[str],
        *,
        publish_error: Exception | None = None,
        sys_id: str = "sn-sys-1",
    ) -> None:
        self.events = events
        self.publish_error = publish_error
        self.sys_id = sys_id
        self.audit_payloads: list[object] = []

    async def invoke(self, tool_name: str, *, context: object, arguments: dict) -> str:
        self.events.append(tool_name)
        if tool_name == "publish_kb_article":
            if self.publish_error is not None:
                raise self.publish_error
            return self.sys_id
        if tool_name == "write_execution_log":
            self.audit_payloads.append(arguments["payload"])
            return "logged"
        raise AssertionError(f"unexpected tool {tool_name}")


def _deps(
    events: list[str],
    *,
    llm_answers: dict | None = None,
    publish_error: Exception | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
    ingest_behavior: str = "ok",
) -> tuple[object, ScriptedRegistry, list]:
    deps = make_deps(llm=FakeLLM(answers=llm_answers or {"article_composer": COMPOSER_ANSWER}))
    registry = ScriptedRegistry(events, publish_error=publish_error)
    object.__setattr__(deps, "tools", registry)

    ingested_articles: list = []

    def fake_ingest(articles, client, *args, **kwargs):
        events.append("ingest")
        if ingest_behavior == "fail":
            raise RuntimeError("qdrant unavailable")
        ingested_articles.extend(articles)
        return 2

    if monkeypatch is not None:
        monkeypatch.setattr("agent.knowledge_capture.ingest_articles", fake_ingest)
    return deps, registry, ingested_articles


def _capture_args(deps, events: list[str]) -> dict:
    async def next_number() -> str:
        return "KB1001"

    return {
        "execution_id": EXECUTION_ID,
        "incident": INCIDENT,
        "solution_text": SOLUTION,
        "deps": deps,
        "next_number": next_number,
        "qdrant_client": object(),
        "kb_sys_id": "kb-sys-1",
    }


class TestHappyPath:
    async def test_order_publish_ingest_audit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events: list[str] = []
        deps, registry, _ = _deps(events, monkeypatch=monkeypatch)
        result = await capture_human_resolution(**_capture_args(deps, events))

        assert events == ["publish_kb_article", "ingest", "write_execution_log"]
        assert result is not None
        assert result.sys_id == "sn-sys-1"
        assert result.article_number == "KB1001"
        assert result.point_count == 2
        assert result.published is True
        assert result.ingested is True

    async def test_audit_payload_well_formed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events: list[str] = []
        deps, registry, _ = _deps(events, monkeypatch=monkeypatch)
        await capture_human_resolution(**_capture_args(deps, events))

        payload = registry.audit_payloads[0]
        assert payload.agent == "knowledge_capture"
        assert payload.action is ExecutionAction.EXECUTE
        assert payload.status is ExecutionStatus.SUCCEEDED
        assert payload.incident_sys_id == "inc-sys-1"
        assert payload.execution_id == EXECUTION_ID
        assert "sn-sys-1" in payload.result and "KB1001" in payload.result

    async def test_qdrant_receives_original_human_resolved_with_sys_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[str] = []
        deps, _, ingested = _deps(events, monkeypatch=monkeypatch)
        await capture_human_resolution(**_capture_args(deps, events))

        article = ingested[0]
        assert article.sys_id == "sn-sys-1"  # stamped before ingest
        assert article.workflow_state is WorkflowState.HUMAN_RESOLVED  # original, not the SN copy


class TestFailureRules:
    async def test_refused_publish_means_no_qdrant_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[str] = []
        deps, _, _ = _deps(
            events,
            monkeypatch=monkeypatch,
            publish_error=RegistryRefusalError(
                RefusalReason.APPROVAL_MISSING,
                tool_name="publish_kb_article",
                permission_class=PermissionClass.HIGH_RISK,
                execution_id=EXECUTION_ID,
            ),
        )
        result = await capture_human_resolution(**_capture_args(deps, events))

        assert result is None
        assert "ingest" not in events  # never touches Qdrant after a refusal

    async def test_publish_error_means_no_qdrant_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[str] = []
        deps, _, _ = _deps(events, monkeypatch=monkeypatch, publish_error=RuntimeError("sn down"))
        result = await capture_human_resolution(**_capture_args(deps, events))

        assert result is None
        assert "ingest" not in events

    async def test_ingest_failure_after_publish_is_drift_not_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[str] = []
        deps, registry, _ = _deps(events, monkeypatch=monkeypatch, ingest_behavior="fail")
        with capture_logs() as logs:
            result = await capture_human_resolution(**_capture_args(deps, events))

        assert result is not None
        assert result.published is True
        assert result.ingested is False
        assert result.point_count == 0
        assert any(e["event"] == "knowledge_capture_drift" for e in logs)
        assert events[-1] == "write_execution_log"  # audit still records the partial state


class TestComposeFailures:
    async def test_retryable_error_retried_once_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[str] = []
        answers = [RetryableError("proxy 503"), COMPOSER_ANSWER]
        deps, _, _ = _deps(events, llm_answers={"article_composer": answers}, monkeypatch=monkeypatch)
        result = await capture_human_resolution(**_capture_args(deps, events))
        assert result is not None

    async def test_retryable_error_exhausted_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[str] = []
        deps, _, _ = _deps(
            events,
            llm_answers={"article_composer": [RetryableError("503"), RetryableError("503")]},
            monkeypatch=monkeypatch,
        )
        result = await capture_human_resolution(**_capture_args(deps, events))
        assert result is None
        assert "ingest" not in events

    async def test_terminal_error_fails_fast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events: list[str] = []
        deps, _, _ = _deps(
            events,
            llm_answers={"article_composer": TerminalError("bad request")},
            monkeypatch=monkeypatch,
        )
        result = await capture_human_resolution(**_capture_args(deps, events))
        assert result is None
        assert len(deps.llm.calls) == 1  # no retry on terminal errors

    async def test_garbage_solution_skips_everything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[str] = []
        deps, registry, _ = _deps(events, monkeypatch=monkeypatch)
        args = _capture_args(deps, events)
        args["solution_text"] = "  "
        result = await capture_human_resolution(**args)

        assert result is None
        assert deps.llm.calls == [] and registry.events == []


class TestIdempotency:
    async def test_double_capture_same_outputs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events: list[str] = []
        deps, _, _ = _deps(events, monkeypatch=monkeypatch)

        async def next_number() -> str:
            return "KB1001"

        kwargs = {
            "execution_id": EXECUTION_ID,
            "incident": INCIDENT,
            "solution_text": SOLUTION,
            "deps": deps,
            "next_number": next_number,
            "qdrant_client": object(),
            "kb_sys_id": "kb-sys-1",
        }
        first = await capture_human_resolution(**kwargs)
        second = await capture_human_resolution(**kwargs)
        assert first == second


class TestNumberAllocation:
    async def test_empty_range_starts_at_kb1001(self) -> None:
        client = FakeKBClient()
        next_number = make_next_article_number(client, "kb-sys-1")
        assert await next_number() == "KB1001"

    async def test_gaps_take_max_plus_one(self) -> None:
        class Client(FakeKBClient):
            async def find_source_ids_by_prefix(self, prefix, *, kb_sys_id=None):
                return ["KB1001-v1.0", "KB1003-v2.0"]

        assert await make_next_article_number(Client(), "kb-sys-1")() == "KB1004"

    async def test_kb11xx_rows_counted(self) -> None:
        class Client(FakeKBClient):
            async def find_source_ids_by_prefix(self, prefix, *, kb_sys_id=None):
                assert prefix == "KB1"  # KB10 would miss KB11xx rows
                return ["KB1001-v1.0", "KB1100-v1.0"]

        assert await make_next_article_number(Client(), "kb-sys-1")() == "KB1101"
