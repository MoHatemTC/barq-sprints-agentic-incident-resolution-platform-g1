"""Trusted ToolRegistry composition for existing ServiceNow actions."""

from __future__ import annotations

from collections.abc import Iterable

from agent.servicenow import IncidentGateway
from agent.tools.permissions import PermissionClass
from agent.tools.refusal_explainer import RefusalExplainer
from agent.tools.registry import (
    ApprovalChecker,
    EnforcementAuditSink,
    ToolRegistration,
    ToolRegistry,
)


def build_servicenow_tool_registry(
    gateway: IncidentGateway,
    *,
    approval_checker: ApprovalChecker,
    audit_sink: EnforcementAuditSink | None = None,
    refusal_explainer: RefusalExplainer | None = None,
    extra_registrations: Iterable[ToolRegistration] = (),
) -> ToolRegistry:
    """Bind the existing gateway methods to their server-owned permissions.

    ``extra_registrations`` lets callers extend the registry (e.g. S3.5's
    ``publish_kb_article``) without editing this builder; duplicates of the
    names below raise at ToolRegistry construction.
    """
    registrations = (
        ToolRegistration(
            "read_incident",
            PermissionClass.READ,
            gateway.read_incident,
        ),
        ToolRegistration(
            "read_conversation",
            PermissionClass.READ,
            gateway.read_conversation,
        ),
        ToolRegistration(
            "find_related_incidents",
            PermissionClass.READ,
            gateway.find_related_incidents,
        ),
        ToolRegistration(
            "write_ai_fields",
            PermissionClass.LOW_RISK_WRITE,
            gateway.write_ai_fields,
        ),
        ToolRegistration(
            "write_work_note",
            PermissionClass.LOW_RISK_WRITE,
            gateway.write_work_note,
        ),
        ToolRegistration(
            "write_execution_log",
            PermissionClass.LOW_RISK_WRITE,
            gateway.write_execution_log,
        ),
        # Fulfilment writes. Low-risk at the registry: whether the agent may use them
        # on a given incident is decided before the call by the autonomy level and the
        # decision table in ``agent.nodes.act`` (risk, evidence, caller present).
        ToolRegistration(
            "assign_incident",
            PermissionClass.LOW_RISK_WRITE,
            gateway.assign_incident,
        ),
        ToolRegistration(
            "resolve_incident",
            PermissionClass.LOW_RISK_WRITE,
            gateway.resolve_incident,
        ),
        ToolRegistration(
            "ask_caller",
            PermissionClass.LOW_RISK_WRITE,
            gateway.ask_caller,
        ),
        *extra_registrations,
    )
    return ToolRegistry(
        registrations,
        approval_checker=approval_checker,
        audit_sink=audit_sink,
        refusal_explainer=refusal_explainer,
    )


__all__ = ["build_servicenow_tool_registry"]
