"""Construction and basic invocation tests for the immutable tool registry."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from structlog.testing import capture_logs

import agent.tools
from agent.tools import PermissionClass, RegistryRefusalError, ToolCallContext, ToolRegistry
from agent.tools.registry import (
    ApprovalCheckResult,
    EnforcementAuditEvent,
    EnforcementDecision,
    RefusalReason,
    StructuredLoggingAuditSink,
    ToolRegistration,
)


class CaptureAudit:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


@pytest.fixture
def context() -> ToolCallContext:
    return ToolCallContext(execution_id=uuid4(), correlation_id="corr-registry-test")


@pytest.mark.asyncio
async def test_read_tool_is_permitted_and_audited_before_dispatch(
    context: ToolCallContext,
) -> None:
    order: list[str] = []

    def handler(*, value: int) -> int:
        order.append("handler")
        return value + 1

    class OrderedAudit(CaptureAudit):
        def emit(self, event: Any) -> None:
            order.append("audit")
            super().emit(event)

    checker = AsyncMock()
    audit = OrderedAudit()
    registry = ToolRegistry(
        [ToolRegistration("read_incident", PermissionClass.READ, handler)],
        approval_checker=checker,
        audit_sink=audit,
    )

    result = await registry.invoke("read_incident", context=context, arguments={"value": 2})

    assert result == 3
    assert order == ["audit", "handler"]
    checker.check.assert_not_awaited()
    event = audit.events[0]
    assert event.tool_name == "read_incident"
    assert event.permission_class is PermissionClass.READ
    assert event.execution_id == str(context.execution_id)
    assert event.decision is EnforcementDecision.PERMITTED
    assert event.correlation_id == context.correlation_id


@pytest.mark.asyncio
async def test_low_risk_write_is_permitted(context: ToolCallContext) -> None:
    handler = AsyncMock(return_value="written")
    checker = AsyncMock()
    audit = CaptureAudit()
    registry = ToolRegistry(
        [ToolRegistration("write_work_note", PermissionClass.LOW_RISK_WRITE, handler)],
        approval_checker=checker,
        audit_sink=audit,
    )

    assert (
        await registry.invoke(
            "write_work_note", context=context, arguments={"note": "redacted from audit"}
        )
        == "written"
    )
    handler.assert_awaited_once_with(note="redacted from audit")
    checker.check.assert_not_awaited()
    assert audit.events[0].permission_class is PermissionClass.LOW_RISK_WRITE


@pytest.mark.asyncio
async def test_unknown_tool_is_blocked_without_handler(context: ToolCallContext) -> None:
    handler = Mock()
    audit = CaptureAudit()
    registry = ToolRegistry(
        [ToolRegistration("known_tool", PermissionClass.READ, handler)],
        approval_checker=AsyncMock(),
        audit_sink=audit,
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke("unknown_tool", context=context, arguments={"secret": "never"})

    assert caught.value.reason is RefusalReason.UNKNOWN_TOOL
    handler.assert_not_called()
    assert audit.events[0].decision is EnforcementDecision.REFUSED
    assert audit.events[0].permission_class is None


def test_duplicate_registration_fails_during_construction() -> None:
    first = ToolRegistration("read_incident", PermissionClass.READ, Mock())
    second = ToolRegistration("read_incident", PermissionClass.HIGH_RISK, Mock())

    with pytest.raises(ValueError, match="duplicate tool registration"):
        ToolRegistry([first, second], approval_checker=AsyncMock(), audit_sink=CaptureAudit())


def test_registration_is_immutable() -> None:
    registration = ToolRegistration("read_incident", PermissionClass.READ, Mock())

    with pytest.raises(AttributeError):
        registration.permission_class = PermissionClass.HIGH_RISK  # type: ignore[misc]


def test_supported_registry_api_does_not_expose_registered_handler() -> None:
    handler = Mock()
    registry = ToolRegistry(
        [ToolRegistration("read_incident", PermissionClass.READ, handler)],
        approval_checker=AsyncMock(),
        audit_sink=CaptureAudit(),
    )

    assert "ToolRegistration" not in agent.tools.__all__
    assert not hasattr(agent.tools, "ToolRegistration")
    assert not hasattr(registry, "registrations")
    assert not hasattr(registry, "get_handler")
    assert not hasattr(registry, "resolve")
    assert not hasattr(registry, "invoke_unchecked")
    assert not hasattr(registry, "invoke_raw")


@pytest.mark.asyncio
async def test_forged_permission_argument_cannot_downgrade_high_risk(
    context: ToolCallContext,
) -> None:
    handler = AsyncMock()
    checker = AsyncMock()
    checker.check.return_value = ApprovalCheckResult(False, RefusalReason.APPROVAL_MISSING)
    registry = ToolRegistry(
        [ToolRegistration("dangerous_action", PermissionClass.HIGH_RISK, handler)],
        approval_checker=checker,
        audit_sink=CaptureAudit(),
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke(
            "dangerous_action",
            context=context,
            arguments={"permission_class": "read"},
        )

    assert caught.value.reason is RefusalReason.APPROVAL_MISSING
    checker.check.assert_awaited_once_with(
        execution_id=context.execution_id, tool_name="dangerous_action"
    )
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_arguments_are_absent_from_permitted_and_refused_audit_records(
    context: ToolCallContext,
) -> None:
    audit = CaptureAudit()
    secret = "do-not-log-this"
    registry = ToolRegistry(
        [ToolRegistration("read_incident", PermissionClass.READ, Mock(return_value=None))],
        approval_checker=AsyncMock(),
        audit_sink=audit,
    )
    await registry.invoke("read_incident", context=context, arguments={"incident_text": secret})

    refused_registry = ToolRegistry([], approval_checker=AsyncMock(), audit_sink=audit)
    with pytest.raises(RegistryRefusalError):
        await refused_registry.invoke(
            "unknown_tool", context=context, arguments={"work_notes": secret}
        )

    assert all(not hasattr(event, "arguments") for event in audit.events)
    assert secret not in repr(audit.events)


@pytest.mark.asyncio
async def test_permit_audit_failure_blocks_handler(context: ToolCallContext) -> None:
    handler = Mock()
    audit = Mock()
    audit.emit.side_effect = OSError("audit storage unavailable")
    registry = ToolRegistry(
        [ToolRegistration("read_incident", PermissionClass.READ, handler)],
        approval_checker=AsyncMock(),
        audit_sink=audit,
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke("read_incident", context=context, arguments={})

    assert caught.value.reason is RefusalReason.AUDIT_UNAVAILABLE
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_refusal_audit_failure_never_dispatches(context: ToolCallContext) -> None:
    handler = Mock()
    audit = Mock()
    audit.emit.side_effect = OSError("audit storage unavailable")
    registry = ToolRegistry(
        [ToolRegistration("known_tool", PermissionClass.READ, handler)],
        approval_checker=AsyncMock(),
        audit_sink=audit,
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke("unknown_tool", context=context, arguments={})

    assert caught.value.reason is RefusalReason.UNKNOWN_TOOL
    handler.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_name",
    [
        " dangerous_action\t\n",
        "api-key=super-secret-value",
        "a" * 129,
    ],
    ids=["whitespace-control", "secret-looking", "overlong"],
)
async def test_invalid_tool_name_is_sanitized_and_blocked_before_dependencies(
    context: ToolCallContext,
    invalid_name: str,
) -> None:
    handler = Mock()
    checker = AsyncMock()
    audit = CaptureAudit()
    registry = ToolRegistry(
        [ToolRegistration("dangerous_action", PermissionClass.HIGH_RISK, handler)],
        approval_checker=checker,
        audit_sink=audit,
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke(invalid_name, context=context, arguments={"secret": "never"})

    assert caught.value.tool_name == "<invalid>"
    assert invalid_name not in str(caught.value)
    handler.assert_not_called()
    checker.check.assert_not_awaited()
    assert len(audit.events) == 1
    assert audit.events[0].tool_name == "<invalid>"
    assert invalid_name not in repr(audit.events[0])


@pytest.mark.asyncio
async def test_intentionally_falsey_audit_sink_is_preserved(
    context: ToolCallContext,
) -> None:
    class FalseyAudit(CaptureAudit):
        def __bool__(self) -> bool:
            return False

    audit = FalseyAudit()
    registry = ToolRegistry(
        [ToolRegistration("read_incident", PermissionClass.READ, Mock(return_value=None))],
        approval_checker=AsyncMock(),
        audit_sink=audit,
    )

    await registry.invoke("read_incident", context=context, arguments={})

    assert len(audit.events) == 1


def test_structured_logging_audit_sink_emits_only_safe_semantic_fields() -> None:
    sink = StructuredLoggingAuditSink()
    event = EnforcementAuditEvent(
        tool_name="dangerous_action",
        permission_class=PermissionClass.HIGH_RISK,
        execution_id=str(uuid4()),
        decision=EnforcementDecision.REFUSED,
        refusal_reason=RefusalReason.APPROVAL_REJECTED,
        approval_id=str(uuid4()),
        correlation_id="corr-safe",
    )

    with capture_logs() as logs:
        sink.emit(event)

    assert len(logs) == 1
    emitted = logs[0]
    assert emitted["event"] == "tool_registry_enforcement"
    assert emitted["log_level"] == "info"
    assert set(emitted) == {
        "event",
        "log_level",
        "tool_name",
        "permission_class",
        "execution_id",
        "decision",
        "refusal_reason",
        "approval_id",
        "correlation_id",
    }
    assert {
        "arguments",
        "incident_text",
        "work_note",
        "work_notes",
        "evidence",
        "approval_evidence",
        "reason",
        "approval_reason",
        "secret",
    }.isdisjoint(emitted)
