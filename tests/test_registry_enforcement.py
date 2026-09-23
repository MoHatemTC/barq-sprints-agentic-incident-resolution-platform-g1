"""Approval lookup and high-risk registry enforcement unit tests."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.sql.elements import BinaryExpression

from agent.tools import PermissionClass, RegistryRefusalError, ToolCallContext, ToolRegistry
from agent.tools.registry import (
    ApprovalCheckResult,
    EnforcementDecision,
    PostgreSQLApprovalChecker,
    RefusalReason,
    ToolRegistration,
)
from app.db.models import Approval


class CaptureAudit:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


class SessionContext(AbstractAsyncContextManager[Any]):
    def __init__(self, session: Any) -> None:
        self.session = session

    async def __aenter__(self) -> Any:
        return self.session

    async def __aexit__(self, *args: Any) -> None:
        return None


def approval(
    execution_id: UUID,
    tool_name: str,
    decision: str,
    decided_at: datetime,
    *,
    evidence: Any = ...,  # noqa: ANN401 - deliberately exercises malformed JSON
) -> Approval:
    return Approval(
        id=uuid4(),
        execution_id=execution_id,
        workflow_state_id=None,
        decision=decision,
        decided_by="operator",
        reason=None,
        evidence={"tool_name": tool_name} if evidence is ... else evidence,
        decided_at=decided_at,
    )


def checker_for_rows(rows: list[Approval]) -> PostgreSQLApprovalChecker:
    result = Mock()
    result.scalars.return_value.all.return_value = rows
    session = Mock()
    session.execute = AsyncMock(return_value=result)
    return PostgreSQLApprovalChecker(Mock(return_value=SessionContext(session)))


def checker_and_session_for_rows(
    rows: list[Approval],
) -> tuple[PostgreSQLApprovalChecker, Mock]:
    result = Mock()
    result.scalars.return_value.all.return_value = rows
    session = Mock()
    session.execute = AsyncMock(return_value=result)
    checker = PostgreSQLApprovalChecker(Mock(return_value=SessionContext(session)))
    return checker, session


async def invoke_high_risk(
    checker: Any,
    execution_id: UUID,
    *,
    arguments: dict[str, Any] | None = None,
) -> tuple[Any, AsyncMock, CaptureAudit]:
    handler = AsyncMock(return_value="done")
    audit = CaptureAudit()
    registry = ToolRegistry(
        [ToolRegistration("dangerous_action", PermissionClass.HIGH_RISK, handler)],
        approval_checker=checker,
        audit_sink=audit,
    )
    result = await registry.invoke(
        "dangerous_action",
        context=ToolCallContext(execution_id, correlation_id="corr-enforcement"),
        arguments=arguments or {},
    )
    return result, handler, audit


@pytest.mark.asyncio
async def test_high_risk_latest_approved_is_permitted() -> None:
    execution_id = uuid4()
    now = datetime.now(UTC)
    old = approval(execution_id, "dangerous_action", "rejected", now - timedelta(minutes=1))
    latest = approval(execution_id, "dangerous_action", "approved", now)

    result, handler, audit = await invoke_high_risk(checker_for_rows([old, latest]), execution_id)

    assert result == "done"
    handler.assert_awaited_once_with()
    assert audit.events[0].decision is EnforcementDecision.PERMITTED
    assert audit.events[0].approval_id == str(latest.id)


@pytest.mark.asyncio
async def test_approval_query_filters_by_current_execution_id() -> None:
    execution_id = uuid4()
    checker, session = checker_and_session_for_rows([])

    await checker.check(execution_id=execution_id, tool_name="dangerous_action")

    query = session.execute.await_args.args[0]
    whereclause = query.whereclause
    assert isinstance(whereclause, BinaryExpression)
    assert whereclause.left.compare(Approval.execution_id.__clause_element__())
    assert whereclause.right.value == execution_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rows_factory", "expected"),
    [
        (lambda execution_id, now: [], RefusalReason.APPROVAL_MISSING),
        (
            lambda execution_id, now: [
                approval(execution_id, "dangerous_action", "approved", now - timedelta(minutes=1)),
                approval(execution_id, "dangerous_action", "rejected", now),
            ],
            RefusalReason.APPROVAL_REJECTED,
        ),
        (
            lambda execution_id, now: [
                approval(execution_id, "dangerous_action", "cancelled", now)
            ],
            RefusalReason.APPROVAL_CANCELLED,
        ),
        (
            lambda execution_id, now: [approval(execution_id, "dangerous_action", "expired", now)],
            RefusalReason.APPROVAL_EXPIRED,
        ),
        (
            lambda execution_id, now: [approval(uuid4(), "dangerous_action", "approved", now)],
            RefusalReason.APPROVAL_MISSING,
        ),
        (
            lambda execution_id, now: [approval(execution_id, "another_tool", "approved", now)],
            RefusalReason.APPROVAL_MISSING,
        ),
        (
            lambda execution_id, now: [
                approval(
                    execution_id,
                    "dangerous_action",
                    "approved",
                    now,
                    evidence={"wrong": "shape"},
                )
            ],
            RefusalReason.APPROVAL_SCOPE_INVALID,
        ),
        (
            lambda execution_id, now: [
                approval(
                    execution_id,
                    "dangerous_action",
                    "approved",
                    now,
                    evidence=[{"tool_name": "dangerous_action"}],
                )
            ],
            RefusalReason.APPROVAL_SCOPE_INVALID,
        ),
        (
            lambda execution_id, now: [
                approval(
                    execution_id,
                    "dangerous_action",
                    "approved",
                    now,
                    evidence={"tool_name": 7},
                )
            ],
            RefusalReason.APPROVAL_SCOPE_INVALID,
        ),
        (
            lambda execution_id, now: [
                approval(execution_id, "dangerous_action", "approved", now),
                approval(execution_id, "dangerous_action", "rejected", now),
            ],
            RefusalReason.APPROVAL_AMBIGUOUS,
        ),
    ],
    ids=[
        "missing",
        "latest-rejected-overrides-approved",
        "cancelled",
        "expired",
        "another-execution",
        "another-tool",
        "missing-tool-name",
        "non-object-evidence",
        "non-string-tool-name",
        "timestamp-ambiguity",
    ],
)
async def test_high_risk_refusal_branches_do_not_dispatch(
    rows_factory: Any, expected: RefusalReason
) -> None:
    execution_id = uuid4()
    now = datetime.now(UTC)
    checker = checker_for_rows(rows_factory(execution_id, now))
    handler = AsyncMock()
    audit = CaptureAudit()
    registry = ToolRegistry(
        [ToolRegistration("dangerous_action", PermissionClass.HIGH_RISK, handler)],
        approval_checker=checker,
        audit_sink=audit,
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke(
            "dangerous_action",
            context=ToolCallContext(execution_id),
            arguments={},
        )

    assert caught.value.reason is expected
    handler.assert_not_awaited()
    assert audit.events[0].decision is EnforcementDecision.REFUSED
    assert audit.events[0].tool_name == "dangerous_action"
    assert audit.events[0].permission_class is PermissionClass.HIGH_RISK
    assert audit.events[0].execution_id == str(execution_id)
    assert audit.events[0].refusal_reason is expected


@pytest.mark.asyncio
async def test_database_error_fails_closed_without_dispatch() -> None:
    execution_id = uuid4()
    session_factory = Mock(side_effect=ConnectionError("postgres unavailable"))
    checker = PostgreSQLApprovalChecker(session_factory)
    handler = AsyncMock()
    audit = CaptureAudit()
    registry = ToolRegistry(
        [ToolRegistration("dangerous_action", PermissionClass.HIGH_RISK, handler)],
        approval_checker=checker,
        audit_sink=audit,
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke(
            "dangerous_action", context=ToolCallContext(execution_id), arguments={}
        )

    assert caught.value.reason is RefusalReason.APPROVAL_CHECK_FAILED
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_approval_checker_exception_fails_closed_without_dispatch() -> None:
    execution_id = uuid4()
    checker = AsyncMock()
    checker.check.side_effect = RuntimeError("checker bug")
    handler = AsyncMock()
    registry = ToolRegistry(
        [ToolRegistration("dangerous_action", PermissionClass.HIGH_RISK, handler)],
        approval_checker=checker,
        audit_sink=CaptureAudit(),
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke(
            "dangerous_action", context=ToolCallContext(execution_id), arguments={}
        )

    assert caught.value.reason is RefusalReason.APPROVAL_CHECK_FAILED
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_approval_checker_result_fails_closed_without_dispatch() -> None:
    execution_id = uuid4()
    checker = AsyncMock()
    checker.check.return_value = {"permitted": True}
    handler = AsyncMock()
    registry = ToolRegistry(
        [ToolRegistration("dangerous_action", PermissionClass.HIGH_RISK, handler)],
        approval_checker=checker,
        audit_sink=CaptureAudit(),
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke(
            "dangerous_action", context=ToolCallContext(execution_id), arguments={}
        )

    assert caught.value.reason is RefusalReason.APPROVAL_CHECK_FAILED
    handler.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        pytest.param(
            lambda execution_id, now: approval(execution_id, "dangerous_action", "unknown", now),
            id="unknown-decision",
        ),
        pytest.param(
            lambda execution_id, now: approval(
                execution_id,
                "dangerous_action",
                "approved",
                None,  # type: ignore[arg-type]
            ),
            id="null-decided-at",
        ),
        pytest.param(
            lambda execution_id, now: approval(
                execution_id,
                "dangerous_action",
                "approved",
                "not-a-timestamp",  # type: ignore[arg-type]
            ),
            id="invalid-decided-at",
        ),
    ],
)
async def test_defensive_approval_row_states_fail_closed_without_dispatch(row: Any) -> None:
    execution_id = uuid4()
    checker = checker_for_rows([row(execution_id, datetime.now(UTC))])
    handler = AsyncMock()
    registry = ToolRegistry(
        [ToolRegistration("dangerous_action", PermissionClass.HIGH_RISK, handler)],
        approval_checker=checker,
        audit_sink=CaptureAudit(),
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke(
            "dangerous_action", context=ToolCallContext(execution_id), arguments={}
        )

    assert caught.value.reason is RefusalReason.APPROVAL_AMBIGUOUS
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_incomparable_approval_timestamps_fail_closed_without_dispatch() -> None:
    execution_id = uuid4()
    aware = datetime.now(UTC)
    naive = aware.replace(tzinfo=None)
    checker = checker_for_rows(
        [
            approval(execution_id, "dangerous_action", "approved", aware),
            approval(execution_id, "dangerous_action", "approved", naive),
        ]
    )
    handler = AsyncMock()
    registry = ToolRegistry(
        [ToolRegistration("dangerous_action", PermissionClass.HIGH_RISK, handler)],
        approval_checker=checker,
        audit_sink=CaptureAudit(),
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke(
            "dangerous_action", context=ToolCallContext(execution_id), arguments={}
        )

    assert caught.value.reason is RefusalReason.APPROVAL_AMBIGUOUS
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_additional_approval_evidence_cannot_downgrade_registered_permission() -> None:
    execution_id = uuid4()
    row = approval(
        execution_id,
        "dangerous_action",
        "approved",
        datetime.now(UTC),
        evidence={
            "tool_name": "dangerous_action",
            "permission_class": "read",
            "secret_metadata": "ignored-for-authorization",
        },
    )
    checker = checker_for_rows([row])

    result, handler, audit = await invoke_high_risk(checker, execution_id)

    assert result == "done"
    handler.assert_awaited_once_with()
    assert audit.events[0].permission_class is PermissionClass.HIGH_RISK


@pytest.mark.asyncio
async def test_fake_checker_can_authorize_without_database_or_servicenow() -> None:
    execution_id = uuid4()
    approval_id = uuid4()
    checker = AsyncMock()
    checker.check.return_value = ApprovalCheckResult(True, approval_id=approval_id)

    result, handler, audit = await invoke_high_risk(checker, execution_id)

    assert result == "done"
    handler.assert_awaited_once()
    checker.check.assert_awaited_once_with(execution_id=execution_id, tool_name="dangerous_action")
    assert audit.events[0].approval_id == str(approval_id)
