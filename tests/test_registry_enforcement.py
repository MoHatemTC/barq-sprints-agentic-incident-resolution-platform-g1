"""Approval lookup and high-risk registry enforcement unit tests."""

from __future__ import annotations

import ast
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, get_type_hints
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.sql.elements import BinaryExpression

from agent.dependencies import AgentDependencies
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


class SessionContext(AbstractContextManager[Any]):
    def __init__(self, session: Any) -> None:
        self.session = session

    def __enter__(self) -> Any:
        return self.session

    def __exit__(self, *args: Any) -> None:
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
    session.execute = Mock(return_value=result)
    return PostgreSQLApprovalChecker(Mock(return_value=SessionContext(session)))


def checker_and_session_for_rows(
    rows: list[Approval],
) -> tuple[PostgreSQLApprovalChecker, Mock]:
    result = Mock()
    result.scalars.return_value.all.return_value = rows
    session = Mock()
    session.execute = Mock(return_value=result)
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

    query = session.execute.call_args.args[0]
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


def malformed_approval_result(
    *, permitted: object, reason: object = None, approval_id: object = None
) -> ApprovalCheckResult:
    result = object.__new__(ApprovalCheckResult)
    object.__setattr__(result, "permitted", permitted)
    object.__setattr__(result, "reason", reason)
    object.__setattr__(result, "approval_id", approval_id)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        pytest.param(
            malformed_approval_result(permitted="yes"),
            id="truthy-string-permitted",
        ),
        pytest.param(
            malformed_approval_result(permitted=1),
            id="integer-permitted",
        ),
        pytest.param(
            malformed_approval_result(
                permitted=None,
                reason=RefusalReason.APPROVAL_MISSING,
            ),
            id="none-permitted",
        ),
        pytest.param(
            malformed_approval_result(permitted=False, reason="approval_missing"),
            id="string-reason",
        ),
        pytest.param(
            malformed_approval_result(
                permitted=True,
                approval_id=object(),
            ),
            id="object-approval-id",
        ),
        pytest.param(
            malformed_approval_result(
                permitted=True,
                approval_id="not-a-uuid",
            ),
            id="malformed-string-approval-id",
        ),
    ],
)
async def test_malformed_approval_result_fields_fail_closed_without_dispatch(
    result: ApprovalCheckResult,
) -> None:
    execution_id = uuid4()
    checker = AsyncMock()
    checker.check.return_value = result
    handler = AsyncMock()
    audit = CaptureAudit()
    explainer = Mock()
    explainer.explain.return_value = "blocked"
    registry = ToolRegistry(
        [ToolRegistration("dangerous_action", PermissionClass.HIGH_RISK, handler)],
        approval_checker=checker,
        audit_sink=audit,
        refusal_explainer=explainer,
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke(
            "dangerous_action", context=ToolCallContext(execution_id), arguments={}
        )

    assert caught.value.reason is RefusalReason.APPROVAL_CHECK_FAILED
    assert caught.value.approval_id is None
    handler.assert_not_awaited()
    assert audit.events[0].refusal_reason is RefusalReason.APPROVAL_CHECK_FAILED
    assert audit.events[0].approval_id is None
    facts = explainer.explain.call_args.args[0]
    assert facts.refusal_reason == RefusalReason.APPROVAL_CHECK_FAILED.value
    assert facts.approval_id is None


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


def test_graph_dependency_exposes_only_the_tool_registry_for_servicenow() -> None:
    fields = AgentDependencies.__dataclass_fields__
    assert "tools" in fields
    assert get_type_hints(AgentDependencies)["tools"] is ToolRegistry
    assert "servicenow" not in fields


_FORBIDDEN_NODE_MODULES = {
    "agent.servicenow",
    "agent.tools.servicenow",
    "app.clients.servicenow_client",
    "app.publishing.servicenow_kb",
    "httpx",
    "requests",
}
_FORBIDDEN_NODE_NAMES = {
    "IncidentGateway",
    "ServiceNowClient",
    "ServiceNowKBClient",
    "ToolRegistration",
}
_FORBIDDEN_REGISTRY_IMPORT_NAMES = {"ToolRegistration"}
# Graph nodes have no legitimate ``.handler`` access.  Treat it as registration
# internals so a statically imported ToolRegistration cannot expose its callable.
_FORBIDDEN_NODE_ATTRIBUTES = {
    "_registrations",
    "_approval_checker",
    "_audit_sink",
    "_refusal_explainer",
    "ToolRegistration",
    "handler",
    "servicenow",
    "read_incident",
    "write_ai_fields",
    "write_work_note",
    "write_execution_log",
}
_SERVICENOW_ENDPOINT_FRAGMENTS = ("/api/now/", "service-now.com", "/oauth_token.do")


def _is_forbidden_node_module(module: str) -> bool:
    return any(
        module == forbidden or module.startswith(f"{forbidden}.")
        for forbidden in _FORBIDDEN_NODE_MODULES
    )


def _graph_node_boundary_violations(source: str, *, filename: str = "<source>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden_node_module(alias.name):
                    violations.append(f"{filename}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported_paths = {module}
            imported_paths.update(
                f"{module}.{alias.name}" if module else alias.name for alias in node.names
            )
            forbidden_paths = sorted(
                path for path in imported_paths if path and _is_forbidden_node_module(path)
            )
            if forbidden_paths:
                violations.append(f"{filename}:{node.lineno}: from {', '.join(forbidden_paths)}")
            if module == "agent.tools.registry":
                forbidden_names = sorted(
                    alias.name
                    for alias in node.names
                    if alias.name in _FORBIDDEN_REGISTRY_IMPORT_NAMES
                )
                if forbidden_names:
                    violations.append(
                        f"{filename}:{node.lineno}: internal registry import "
                        f"{', '.join(forbidden_names)}"
                    )
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NODE_NAMES:
            violations.append(f"{filename}:{node.lineno}: {node.id}")
        elif isinstance(node, ast.Attribute) and (
            node.attr in _FORBIDDEN_NODE_ATTRIBUTES or node.attr in _FORBIDDEN_NODE_NAMES
        ):
            violations.append(f"{filename}:{node.lineno}: .{node.attr}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            if any(fragment in lowered for fragment in _SERVICENOW_ENDPOINT_FRAGMENTS):
                violations.append(f"{filename}:{node.lineno}: ServiceNow endpoint literal")

    return violations


@pytest.mark.parametrize(
    "source, expected",
    [
        ("from agent import servicenow", "agent.servicenow"),
        (
            "from agent import servicenow as sn\nx = sn.IncidentGateway",
            "agent.servicenow",
        ),
        ("from app.clients import servicenow_client", "app.clients.servicenow_client"),
        (
            "from app.clients import servicenow_client as sn_client\n"
            "x = sn_client.ServiceNowClient",
            "app.clients.servicenow_client",
        ),
        ("from app.publishing import servicenow_kb", "app.publishing.servicenow_kb"),
        (
            "from app.publishing import servicenow_kb as kb\nx = kb.ServiceNowKBClient",
            "app.publishing.servicenow_kb",
        ),
        ("import agent.servicenow", "agent.servicenow"),
        ("import agent.servicenow as sn", "agent.servicenow"),
        ("from agent.servicenow import IncidentGateway", "agent.servicenow"),
        ("from agent.servicenow import IncidentGateway as IG", "agent.servicenow"),
        ("import agent.tools.servicenow", "agent.tools.servicenow"),
        ("import agent.tools.servicenow as sn_tools", "agent.tools.servicenow"),
        ("from agent.tools import servicenow", "agent.tools.servicenow"),
        ("from agent.tools import servicenow as sn_tools", "agent.tools.servicenow"),
        (
            "from agent.tools.servicenow import ToolRegistration",
            "agent.tools.servicenow",
        ),
        (
            "from agent.tools.servicenow import ToolRegistration as T",
            "agent.tools.servicenow",
        ),
        (
            "from app.clients.servicenow_client import ServiceNowClient",
            "app.clients.servicenow_client",
        ),
        (
            "from app.clients.servicenow_client import ServiceNowClient as SNC",
            "app.clients.servicenow_client",
        ),
    ],
)
def test_graph_node_boundary_rejects_forbidden_import_variants(source: str, expected: str) -> None:
    assert any(expected in violation for violation in _graph_node_boundary_violations(source))


@pytest.mark.parametrize(
    "source, expected",
    [
        ("x = sn.IncidentGateway", ".IncidentGateway"),
        ("x = sn_client.ServiceNowClient", ".ServiceNowClient"),
        ("x = kb.ServiceNowKBClient", ".ServiceNowKBClient"),
    ],
)
def test_graph_node_boundary_rejects_forbidden_class_attributes(source: str, expected: str) -> None:
    assert any(expected in violation for violation in _graph_node_boundary_violations(source))


def test_graph_node_boundary_allows_unrelated_parent_package_imports() -> None:
    source = """\
from agent import errors
from app.clients import base
from app.publishing import payload
"""
    assert _graph_node_boundary_violations(source) == []


@pytest.mark.parametrize(
    "source, expected",
    [
        ("x = deps.tools._registrations", "._registrations"),
        ("x = deps.tools._approval_checker", "._approval_checker"),
        ("x = deps.tools._audit_sink", "._audit_sink"),
        ("x = deps.tools._refusal_explainer", "._refusal_explainer"),
        (
            'deps.tools._registrations["write_ai_fields"].handler(...)',
            "._registrations",
        ),
        ("registration.handler()", ".handler"),
        (
            "from agent.tools.registry import ToolRegistration",
            "internal registry import ToolRegistration",
        ),
        (
            "from agent.tools.registry import ToolRegistration as T",
            "internal registry import ToolRegistration",
        ),
        ("x = registry.ToolRegistration", ".ToolRegistration"),
    ],
)
def test_graph_node_boundary_rejects_private_registry_authority(source: str, expected: str) -> None:
    assert any(expected in violation for violation in _graph_node_boundary_violations(source))


def test_graph_node_boundary_allows_public_registry_invocation() -> None:
    source = """\
from agent.tools import ToolCallContext, ToolRegistry

result = deps.tools.invoke(
    "read_incident",
    context=ToolCallContext(execution_id),
    arguments={},
)
"""
    assert _graph_node_boundary_violations(source) == []


def test_graph_nodes_cannot_bypass_the_tool_registry() -> None:
    nodes_root = Path(__file__).parents[1] / "src" / "agent" / "nodes"
    violations: list[str] = []

    for path in sorted(nodes_root.rglob("*.py")):
        violations.extend(
            _graph_node_boundary_violations(
                path.read_text(encoding="utf-8"),
                filename=str(path),
            )
        )

    assert violations == []
