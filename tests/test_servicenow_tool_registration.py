"""Registration tests for the existing ServiceNow gateway actions."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock, create_autospec
from uuid import uuid4

import pytest

from agent.servicenow import IncidentGateway
from agent.tools import (
    PermissionClass,
    RegistryRefusalError,
    ToolCallContext,
    build_servicenow_tool_registry,
)
from agent.tools.registry import EnforcementAuditEvent, RefusalReason
from app.models.execution_log import ExecutionLogCreatePayload
from app.models.incident import IncidentUpdatePayload
from app.workers.retry_policy import RetryableError


class CaptureAudit:
    def __init__(self) -> None:
        self.events: list[EnforcementAuditEvent] = []

    def emit(self, event: EnforcementAuditEvent) -> None:
        self.events.append(event)


@pytest.fixture
def context() -> ToolCallContext:
    return ToolCallContext(execution_id=uuid4(), correlation_id="corr-servicenow-tools")


@pytest.fixture
def gateway() -> Mock:
    return create_autospec(IncidentGateway, instance=True)


@pytest.fixture
def approval_checker() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def audit_sink() -> CaptureAudit:
    return CaptureAudit()


def build_registry(
    gateway: Mock,
    approval_checker: AsyncMock,
    audit_sink: CaptureAudit,
) -> Any:
    return build_servicenow_tool_registry(
        gateway,
        approval_checker=approval_checker,
        audit_sink=audit_sink,
    )


@pytest.mark.asyncio
async def test_all_four_core_actions_have_exact_server_owned_permissions(
    context: ToolCallContext,
    gateway: Mock,
    approval_checker: AsyncMock,
    audit_sink: CaptureAudit,
) -> None:
    registry = build_registry(gateway, approval_checker, audit_sink)
    invocations = (
        ("read_incident", {"sys_id": "incident-1"}),
        (
            "write_ai_fields",
            {"sys_id": "incident-1", "payload": IncidentUpdatePayload()},
        ),
        ("write_work_note", {"sys_id": "incident-1", "note": "Investigating"}),
        (
            "write_execution_log",
            {
                "sys_id": "incident-1",
                "payload": Mock(spec=ExecutionLogCreatePayload),
            },
        ),
    )

    for name, arguments in invocations:
        await registry.invoke(name, context=context, arguments=arguments)

    assert {event.tool_name: event.permission_class for event in audit_sink.events} == {
        "read_incident": PermissionClass.READ,
        "write_ai_fields": PermissionClass.LOW_RISK_WRITE,
        "write_work_note": PermissionClass.LOW_RISK_WRITE,
        "write_execution_log": PermissionClass.LOW_RISK_WRITE,
    }
    approval_checker.check.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_incident_delegates_once_and_returns_result_unchanged(
    context: ToolCallContext,
    gateway: Mock,
    approval_checker: AsyncMock,
    audit_sink: CaptureAudit,
) -> None:
    expected = {"sys_id": "incident-1", "number": "INC0001"}
    gateway.read_incident.return_value = expected
    registry = build_registry(gateway, approval_checker, audit_sink)

    result = await registry.invoke(
        "read_incident",
        context=context,
        arguments={"sys_id": "incident-1"},
    )

    assert result is expected
    gateway.read_incident.assert_called_once_with(sys_id="incident-1")
    assert audit_sink.events[0].permission_class is PermissionClass.READ


@pytest.mark.asyncio
async def test_write_ai_fields_delegates_exact_arguments_without_approval_lookup(
    context: ToolCallContext,
    gateway: Mock,
    approval_checker: AsyncMock,
    audit_sink: CaptureAudit,
) -> None:
    payload = IncidentUpdatePayload(
        # A real field. This previously read ``ai_summary``, which is not one of
        # the scoped app's columns, so it was accepted and then silently dropped
        # from the write body — the exact failure IncidentUpdatePayload now
        # forbids.
        ai_suggestion="VPN restored",
        work_notes="AI: reset the client",
        ai_human_review_required=False,
    )
    registry = build_registry(gateway, approval_checker, audit_sink)

    await registry.invoke(
        "write_ai_fields",
        context=context,
        arguments={"sys_id": "incident-2", "payload": payload},
    )

    gateway.write_ai_fields.assert_called_once_with(sys_id="incident-2", payload=payload)
    approval_checker.check.assert_not_awaited()
    assert audit_sink.events[0].permission_class is PermissionClass.LOW_RISK_WRITE


@pytest.mark.asyncio
async def test_write_work_note_delegates_exact_arguments_without_approval_lookup(
    context: ToolCallContext,
    gateway: Mock,
    approval_checker: AsyncMock,
    audit_sink: CaptureAudit,
) -> None:
    registry = build_registry(gateway, approval_checker, audit_sink)

    await registry.invoke(
        "write_work_note",
        context=context,
        arguments={"sys_id": "incident-3", "note": "AI: troubleshooting started"},
    )

    gateway.write_work_note.assert_called_once_with(
        sys_id="incident-3", note="AI: troubleshooting started"
    )
    approval_checker.check.assert_not_awaited()
    assert audit_sink.events[0].permission_class is PermissionClass.LOW_RISK_WRITE


@pytest.mark.asyncio
async def test_write_execution_log_is_a_gateway_tool_not_the_registry_audit(
    context: ToolCallContext,
    gateway: Mock,
    approval_checker: AsyncMock,
    audit_sink: CaptureAudit,
) -> None:
    payload = Mock(spec=ExecutionLogCreatePayload)
    registry = build_registry(gateway, approval_checker, audit_sink)

    await registry.invoke(
        "write_execution_log",
        context=context,
        arguments={"sys_id": "incident-4", "payload": payload},
    )

    gateway.write_execution_log.assert_called_once_with(sys_id="incident-4", payload=payload)
    approval_checker.check.assert_not_awaited()
    assert len(audit_sink.events) == 1
    assert audit_sink.events[0].tool_name == "write_execution_log"
    assert audit_sink.events[0].permission_class is PermissionClass.LOW_RISK_WRITE


@pytest.mark.asyncio
async def test_gateway_exception_is_propagated_unchanged(
    context: ToolCallContext,
    gateway: Mock,
    approval_checker: AsyncMock,
    audit_sink: CaptureAudit,
) -> None:
    expected = RetryableError("ServiceNow transient failure")
    gateway.read_incident.side_effect = expected
    registry = build_registry(gateway, approval_checker, audit_sink)

    with pytest.raises(RetryableError) as caught:
        await registry.invoke(
            "read_incident",
            context=context,
            arguments={"sys_id": "incident-5"},
        )

    assert caught.value is expected
    gateway.read_incident.assert_called_once_with(sys_id="incident-5")


@pytest.mark.asyncio
async def test_invocation_argument_cannot_override_registered_permission(
    context: ToolCallContext,
    gateway: Mock,
    approval_checker: AsyncMock,
    audit_sink: CaptureAudit,
) -> None:
    registry = build_registry(gateway, approval_checker, audit_sink)

    with pytest.raises(TypeError):
        await registry.invoke(
            "read_incident",
            context=context,
            arguments={"sys_id": "incident-6", "permission_class": "high_risk"},
        )

    gateway.read_incident.assert_not_called()
    approval_checker.check.assert_not_awaited()
    assert audit_sink.events[0].permission_class is PermissionClass.READ


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["search_knowledge", "flag_human_review"])
async def test_legacy_gateway_actions_are_not_added_as_core_registrations(
    name: str,
    context: ToolCallContext,
    gateway: Mock,
    approval_checker: AsyncMock,
    audit_sink: CaptureAudit,
) -> None:
    registry = build_registry(gateway, approval_checker, audit_sink)

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke(name, context=context, arguments={})

    assert caught.value.reason is RefusalReason.UNKNOWN_TOOL
