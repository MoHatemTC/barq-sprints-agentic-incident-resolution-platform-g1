"""Immutable tool registry with server-side permission enforcement.

The registry is the sole invocation boundary.  A caller selects a registered
name and supplies handler arguments, but the effective permission always comes
from the private registration map.  High-risk authorization is read directly
from PostgreSQL approval records and every enforcement verdict is audited before
handler dispatch.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID

import structlog
from sqlalchemy import select

from agent.tools.permissions import PermissionClass
from agent.tools.refusal_explainer import RefusalExplainer, RefusalFacts, fallback_explanation
from app.db.models import Approval
from app.workers.retry_policy import TerminalError
from app.workers.sync_engine import SyncSessionFactory

MAX_TOOL_NAME_LENGTH = 128
_TOOL_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,127}\Z")
_SEALED_REGISTRY_AUTHORITY = frozenset(
    {
        "_registrations",
        "_approval_checker",
        "_audit_sink",
        "_refusal_explainer",
    }
)

type ToolArguments = Mapping[str, Any]
type ToolHandler = Callable[..., Any]


class RefusalReason(StrEnum):
    """Stable policy reason codes; callers never need to parse error text."""

    UNKNOWN_TOOL = "unknown_tool"
    APPROVAL_MISSING = "approval_missing"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_CANCELLED = "approval_cancelled"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_SCOPE_INVALID = "approval_scope_invalid"
    APPROVAL_AMBIGUOUS = "approval_ambiguous"
    APPROVAL_CHECK_FAILED = "approval_check_failed"
    INVALID_CONTEXT = "invalid_context"
    AUDIT_UNAVAILABLE = "audit_unavailable"


class EnforcementDecision(StrEnum):
    PERMITTED = "permitted"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class ToolRegistration:
    """An immutable, server-owned binding of a name, permission, and handler."""

    name: str
    permission_class: PermissionClass
    handler: ToolHandler

    def __post_init__(self) -> None:
        normalized = _normalize_tool_name(self.name)
        if normalized != self.name:
            raise ValueError("registered tool names must already be normalized")
        if not isinstance(self.permission_class, PermissionClass):
            raise TypeError("permission_class must be a PermissionClass")
        if not callable(self.handler):
            raise TypeError("handler must be callable")


@dataclass(frozen=True, slots=True)
class ToolCallContext:
    """Orchestrator-supplied identity, structurally validated before dispatch."""

    execution_id: UUID | str
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalCheckResult:
    """A high-risk authorization verdict returned by an approval checker."""

    permitted: bool
    reason: RefusalReason | None = None
    approval_id: UUID | str | None = None

    def __post_init__(self) -> None:
        if self.permitted and self.reason is not None:
            raise ValueError("a permitted approval result cannot have a refusal reason")
        if not self.permitted and self.reason is None:
            raise ValueError("a refused approval result requires a refusal reason")


class ApprovalChecker(Protocol):
    """Authoritative high-risk approval lookup contract."""

    def check(
        self, *, execution_id: UUID | str, tool_name: str
    ) -> Awaitable[ApprovalCheckResult]: ...


@dataclass(frozen=True, slots=True)
class EnforcementAuditEvent:
    """Argument-free registry decision record suitable for security auditing."""

    tool_name: str
    permission_class: PermissionClass | None
    execution_id: str
    decision: EnforcementDecision
    refusal_reason: RefusalReason | None = None
    approval_id: str | None = None
    correlation_id: str | None = None


class EnforcementAuditSink(Protocol):
    """Synchronous audit sink called before dispatch can begin."""

    def emit(self, event: EnforcementAuditEvent) -> None: ...


class StructuredLoggingAuditSink:
    """Production audit sink using the project's structured server logger."""

    def __init__(self) -> None:
        self._logger = structlog.get_logger("agent.tools.registry")

    def emit(self, event: EnforcementAuditEvent) -> None:
        self._logger.info(
            "tool_registry_enforcement",
            tool_name=event.tool_name,
            permission_class=(
                event.permission_class.value if event.permission_class is not None else None
            ),
            execution_id=event.execution_id,
            decision=event.decision.value,
            refusal_reason=(
                event.refusal_reason.value if event.refusal_reason is not None else None
            ),
            approval_id=event.approval_id,
            correlation_id=event.correlation_id,
        )


class RegistryRefusalError(TerminalError):
    """Terminal, structured refusal raised by the registry policy boundary."""

    def __init__(
        self,
        reason: RefusalReason,
        *,
        tool_name: str,
        permission_class: PermissionClass | None,
        execution_id: UUID | str,
        approval_id: UUID | str | None = None,
    ) -> None:
        self.reason = reason
        self.reason_code = reason.value
        self.tool_name = tool_name
        self.permission_class = permission_class
        self.execution_id = str(execution_id)
        self.approval_id = str(approval_id) if approval_id is not None else None
        self.explanation = fallback_explanation(reason.value)
        super().__init__(f"tool invocation refused: {reason.value}")


class PostgreSQLApprovalChecker:
    """Read immutable approval decisions directly from PostgreSQL.

    Rows are restricted to the current execution in SQL.  Tool scope is then
    validated from the JSON evidence object so malformed evidence can be
    distinguished from a genuine absence of a matching approval.
    """

    def __init__(self, session_factory: SyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def check(self, *, execution_id: UUID | str, tool_name: str) -> ApprovalCheckResult:
        try:
            execution_uuid = (
                execution_id if isinstance(execution_id, UUID) else UUID(str(execution_id))
            )
            query = (
                select(Approval)
                .where(Approval.execution_id == execution_uuid)
                .order_by(Approval.decided_at.desc())
            )
            with self._session_factory() as session:
                result = session.execute(query)
                rows = list(result.scalars().all())
        except Exception:
            return ApprovalCheckResult(False, RefusalReason.APPROVAL_CHECK_FAILED)

        matching: list[Approval] = []
        malformed_scope = False
        for row in rows:
            if row.execution_id != execution_uuid:
                continue
            evidence = row.evidence
            if not isinstance(evidence, Mapping):
                malformed_scope = True
                continue
            scoped_name = evidence.get("tool_name")
            if not isinstance(scoped_name, str) or not _is_normalized_tool_name(scoped_name):
                malformed_scope = True
                continue
            if scoped_name == tool_name:
                matching.append(row)

        if malformed_scope:
            return ApprovalCheckResult(False, RefusalReason.APPROVAL_SCOPE_INVALID)
        if not matching:
            return ApprovalCheckResult(False, RefusalReason.APPROVAL_MISSING)

        if any(not isinstance(row.decided_at, datetime) for row in matching):
            return ApprovalCheckResult(False, RefusalReason.APPROVAL_AMBIGUOUS)

        try:
            newest_timestamp = max(row.decided_at for row in matching)
        except (TypeError, ValueError):
            return ApprovalCheckResult(False, RefusalReason.APPROVAL_AMBIGUOUS)
        latest = [row for row in matching if row.decided_at == newest_timestamp]
        if len(latest) != 1:
            return ApprovalCheckResult(False, RefusalReason.APPROVAL_AMBIGUOUS)

        approval = latest[0]
        refusal_by_decision = {
            "rejected": RefusalReason.APPROVAL_REJECTED,
            "cancelled": RefusalReason.APPROVAL_CANCELLED,
            "expired": RefusalReason.APPROVAL_EXPIRED,
        }
        if approval.decision == "approved":
            return ApprovalCheckResult(True, approval_id=approval.id)
        reason = refusal_by_decision.get(approval.decision)
        if reason is None:
            return ApprovalCheckResult(
                False,
                RefusalReason.APPROVAL_AMBIGUOUS,
                approval_id=approval.id,
            )
        return ApprovalCheckResult(False, reason, approval_id=approval.id)


class ToolRegistry:
    """Fixed registry and authoritative tool invocation path."""

    def __init__(
        self,
        registrations: Iterable[ToolRegistration],
        *,
        approval_checker: ApprovalChecker,
        audit_sink: EnforcementAuditSink | None = None,
        refusal_explainer: RefusalExplainer | None = None,
    ) -> None:
        copied: dict[str, ToolRegistration] = {}
        for registration in registrations:
            if registration.name in copied:
                raise ValueError(f"duplicate tool registration: {registration.name}")
            copied[registration.name] = registration
        self._registrations: Mapping[str, ToolRegistration] = MappingProxyType(copied)
        self._approval_checker = approval_checker
        self._audit_sink = audit_sink if audit_sink is not None else StructuredLoggingAuditSink()
        self._refusal_explainer = refusal_explainer

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _SEALED_REGISTRY_AUTHORITY and hasattr(self, name):
            raise AttributeError(f"{name} is sealed after ToolRegistry construction")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        if name in _SEALED_REGISTRY_AUTHORITY:
            raise AttributeError(f"{name} is sealed after ToolRegistry construction")
        object.__delattr__(self, name)

    async def invoke(
        self,
        tool_name: str,
        *,
        context: ToolCallContext,
        arguments: ToolArguments,
    ) -> Any:
        """Enforce registry policy, audit the verdict, then dispatch the handler."""
        if not _is_valid_execution_id(context.execution_id):
            try:
                context_tool_name = _normalize_tool_name(tool_name)
            except (TypeError, ValueError):
                context_tool_name = "<invalid>"
            context_registration = self._registrations.get(context_tool_name)
            refusal = RegistryRefusalError(
                RefusalReason.INVALID_CONTEXT,
                tool_name=context_tool_name,
                permission_class=(
                    context_registration.permission_class
                    if context_registration is not None
                    else None
                ),
                execution_id="<invalid>",
            )
            self._audit_refusal(refusal, context)
            self._explain_refusal(refusal)
            raise refusal

        try:
            normalized_name = _normalize_tool_name(tool_name)
        except (TypeError, ValueError):
            refusal = RegistryRefusalError(
                RefusalReason.UNKNOWN_TOOL,
                tool_name="<invalid>",
                permission_class=None,
                execution_id=context.execution_id,
            )
            self._audit_refusal(refusal, context)
            self._explain_refusal(refusal)
            raise refusal from None

        registration = self._registrations.get(normalized_name)
        if registration is None:
            refusal = RegistryRefusalError(
                RefusalReason.UNKNOWN_TOOL,
                tool_name=normalized_name,
                permission_class=None,
                execution_id=context.execution_id,
            )
            self._audit_refusal(refusal, context)
            self._explain_refusal(refusal)
            raise refusal

        approval_id: UUID | str | None = None
        if registration.permission_class is PermissionClass.HIGH_RISK:
            try:
                approval = await self._approval_checker.check(
                    execution_id=context.execution_id,
                    tool_name=registration.name,
                )
                if not _is_valid_approval_check_result(approval):
                    raise TypeError("approval checker returned an invalid result")
            except Exception:
                approval = ApprovalCheckResult(False, RefusalReason.APPROVAL_CHECK_FAILED)

            approval_id = approval.approval_id
            if not approval.permitted:
                refusal = RegistryRefusalError(
                    approval.reason or RefusalReason.APPROVAL_CHECK_FAILED,
                    tool_name=registration.name,
                    permission_class=registration.permission_class,
                    execution_id=context.execution_id,
                    approval_id=approval_id,
                )
                self._audit_refusal(refusal, context)
                self._explain_refusal(refusal)
                raise refusal

        permit_event = EnforcementAuditEvent(
            tool_name=registration.name,
            permission_class=registration.permission_class,
            execution_id=str(context.execution_id),
            decision=EnforcementDecision.PERMITTED,
            approval_id=str(approval_id) if approval_id is not None else None,
            correlation_id=context.correlation_id,
        )
        try:
            self._audit_sink.emit(permit_event)
        except Exception as exc:
            refusal = RegistryRefusalError(
                RefusalReason.AUDIT_UNAVAILABLE,
                tool_name=registration.name,
                permission_class=registration.permission_class,
                execution_id=context.execution_id,
                approval_id=approval_id,
            )
            self._audit_refusal(refusal, context)
            self._explain_refusal(refusal)
            raise refusal from exc

        handler_result = registration.handler(**dict(arguments))
        if inspect.isawaitable(handler_result):
            return await handler_result
        return handler_result

    def _audit_refusal(self, refusal: RegistryRefusalError, context: ToolCallContext) -> None:
        event = EnforcementAuditEvent(
            tool_name=refusal.tool_name,
            permission_class=refusal.permission_class,
            execution_id=refusal.execution_id,
            decision=EnforcementDecision.REFUSED,
            refusal_reason=refusal.reason,
            approval_id=refusal.approval_id,
            correlation_id=context.correlation_id,
        )
        try:
            self._audit_sink.emit(event)
        except Exception:
            # The request was already refused.  Preserve that policy decision;
            # an unavailable audit sink must never turn refusal into dispatch.
            return

    def _explain_refusal(self, refusal: RegistryRefusalError) -> None:
        if self._refusal_explainer is None:
            return
        try:
            facts = RefusalFacts(
                tool_name=refusal.tool_name,
                permission_class=refusal.permission_class,
                execution_id=refusal.execution_id,
                refusal_reason=refusal.reason_code,
                approval_id=refusal.approval_id,
            )
            explanation = self._refusal_explainer.explain(facts)
            if isinstance(explanation, str) and explanation.strip():
                refusal.explanation = explanation.strip()
        except Exception:
            # Explanation is presentation only.  Preserve the final refusal and
            # the deterministic fallback already attached to the exception.
            return


def _is_normalized_tool_name(name: str) -> bool:
    return len(name) <= MAX_TOOL_NAME_LENGTH and _TOOL_NAME_PATTERN.fullmatch(name) is not None


def _normalize_tool_name(name: str) -> str:
    if not isinstance(name, str):
        raise TypeError("tool name must be a string")
    if not _is_normalized_tool_name(name):
        raise ValueError("tool name is invalid")
    return name


def _is_valid_execution_id(execution_id: object) -> bool:
    if isinstance(execution_id, UUID):
        return True
    if not isinstance(execution_id, str) or not execution_id.strip():
        return False
    try:
        UUID(execution_id)
    except (ValueError, AttributeError):
        return False
    return True


def _is_valid_approval_check_result(result: object) -> bool:
    if not isinstance(result, ApprovalCheckResult):
        return False
    if type(result.permitted) is not bool:
        return False
    if result.reason is not None and not isinstance(result.reason, RefusalReason):
        return False
    if result.approval_id is not None:
        if isinstance(result.approval_id, UUID):
            pass
        elif isinstance(result.approval_id, str):
            try:
                UUID(result.approval_id)
            except (ValueError, AttributeError):
                return False
        else:
            return False
    if result.permitted:
        return result.reason is None
    return result.reason is not None


__all__ = [
    "ApprovalCheckResult",
    "ApprovalChecker",
    "EnforcementAuditEvent",
    "EnforcementAuditSink",
    "EnforcementDecision",
    "MAX_TOOL_NAME_LENGTH",
    "PostgreSQLApprovalChecker",
    "RefusalReason",
    "RegistryRefusalError",
    "StructuredLoggingAuditSink",
    "ToolCallContext",
    "ToolRegistration",
    "ToolRegistry",
]
