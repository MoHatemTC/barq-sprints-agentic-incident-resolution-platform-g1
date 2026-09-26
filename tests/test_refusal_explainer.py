"""Refusal explanations remain presentation-only and fail safely."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent.prompts import REFUSAL_EXPLAINER_SYSTEM, RefusalExplanation
from agent.tools import (
    PermissionClass,
    RefusalExplainer,
    RefusalFacts,
    RegistryRefusalError,
    ToolCallContext,
    ToolRegistry,
)
from agent.tools.refusal_explainer import fallback_explanation
from agent.tools.registry import (
    ApprovalCheckResult,
    EnforcementAuditEvent,
    RefusalReason,
    ToolRegistration,
)
from app.workers.retry_policy import RetryableError, TerminalError


@dataclass
class RecordingLLM:
    answer: Any
    order: list[str] | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    model_name: str = "gemini/test"

    def structured(
        self,
        *,
        purpose: str,
        system: str,
        prompt: str,
        schema: type[Any],
    ) -> Any:
        if self.order is not None:
            self.order.append("explain")
        self.calls.append(
            {"purpose": purpose, "system": system, "prompt": prompt, "schema": schema}
        )
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


class SchemaValidatingLLM(RecordingLLM):
    """Fake the production structured boundary, including Pydantic validation."""

    def structured(
        self,
        *,
        purpose: str,
        system: str,
        prompt: str,
        schema: type[Any],
    ) -> Any:
        answer = super().structured(
            purpose=purpose,
            system=system,
            prompt=prompt,
            schema=schema,
        )
        return schema.model_validate(answer)


class CaptureAudit:
    def __init__(self, order: list[str] | None = None) -> None:
        self.events: list[EnforcementAuditEvent] = []
        self.order = order

    def emit(self, event: EnforcementAuditEvent) -> None:
        if self.order is not None:
            self.order.append("audit")
        self.events.append(event)


class FailingAudit:
    def __init__(self, order: list[str]) -> None:
        self.events: list[EnforcementAuditEvent] = []
        self.order = order

    def emit(self, event: EnforcementAuditEvent) -> None:
        self.events.append(event)
        self.order.append(f"audit:{event.decision.value}")
        if event.decision.value == "permitted":
            raise OSError("permitted audit failed")
        raise RuntimeError("refusal audit failed")


class CapturingExplainer:
    def __init__(self, llm: RecordingLLM) -> None:
        self.delegate = RefusalExplainer(llm)
        self.facts: list[RefusalFacts] = []

    def explain(self, facts: RefusalFacts) -> str:
        self.facts.append(facts)
        return self.delegate.explain(facts)


def high_risk_registry(
    *,
    llm: RecordingLLM,
    approval: ApprovalCheckResult | BaseException,
    handler: AsyncMock | None = None,
    audit: CaptureAudit | Mock | None = None,
) -> tuple[ToolRegistry, AsyncMock, CapturingExplainer, CaptureAudit | Mock]:
    checked = AsyncMock()
    checked.check.side_effect = approval if isinstance(approval, BaseException) else None
    if not isinstance(approval, BaseException):
        checked.check.return_value = approval
    actual_handler = handler or AsyncMock(return_value="should-not-run")
    actual_audit = audit or CaptureAudit()
    explainer = CapturingExplainer(llm)
    registry = ToolRegistry(
        [ToolRegistration("dangerous_action", PermissionClass.HIGH_RISK, actual_handler)],
        approval_checker=checked,
        audit_sink=actual_audit,
        refusal_explainer=explainer,  # type: ignore[arg-type]
    )
    return registry, actual_handler, explainer, actual_audit


def prompt_facts(call: dict[str, Any]) -> dict[str, Any]:
    return json.loads(str(call["prompt"]).splitlines()[-1])


@pytest.mark.asyncio
async def test_successful_explanation_is_attached_after_refusal_audit() -> None:
    order: list[str] = []
    llm = RecordingLLM(RefusalExplanation(message="Approval was not found."), order=order)
    audit = CaptureAudit(order)
    registry, handler, explainer, _ = high_risk_registry(
        llm=llm,
        approval=ApprovalCheckResult(False, RefusalReason.APPROVAL_MISSING),
        audit=audit,
    )
    context = ToolCallContext(uuid4())

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke("dangerous_action", context=context, arguments={})

    assert order == ["audit", "explain"]
    assert caught.value.reason is RefusalReason.APPROVAL_MISSING
    assert caught.value.explanation == "Approval was not found."
    assert explainer.facts == [
        RefusalFacts(
            tool_name="dangerous_action",
            permission_class=PermissionClass.HIGH_RISK,
            execution_id=str(context.execution_id),
            refusal_reason="approval_missing",
            approval_id=None,
        )
    ]
    assert prompt_facts(llm.calls[0]) == explainer.facts[0].model_dump(mode="json")
    assert llm.calls[0]["purpose"] == "tool_refusal"
    assert llm.calls[0]["schema"] is RefusalExplanation
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_explainer_is_not_called_for_permitted_tools() -> None:
    llm = RecordingLLM(RefusalExplanation(message="must not be used"))
    handler = AsyncMock(return_value="done")
    registry = ToolRegistry(
        [ToolRegistration("read_incident", PermissionClass.READ, handler)],
        approval_checker=AsyncMock(),
        audit_sink=CaptureAudit(),
        refusal_explainer=RefusalExplainer(llm),
    )

    result = await registry.invoke(
        "read_incident",
        context=ToolCallContext(uuid4()),
        arguments={"sys_id": "safe"},
    )

    assert result == "done"
    assert llm.calls == []
    handler.assert_awaited_once_with(sys_id="safe")


@pytest.mark.asyncio
async def test_unknown_and_invalid_tool_names_use_safe_explanation_facts() -> None:
    raw_invalid = " api-key=raw-secret\n"
    for requested_name, expected_name in (
        ("missing_tool", "missing_tool"),
        (raw_invalid, "<invalid>"),
    ):
        llm = RecordingLLM(RefusalExplanation(message="Server policy blocked the tool."))
        explainer = CapturingExplainer(llm)
        audit = CaptureAudit()
        registry = ToolRegistry(
            [],
            approval_checker=AsyncMock(),
            audit_sink=audit,
            refusal_explainer=explainer,  # type: ignore[arg-type]
        )

        with pytest.raises(RegistryRefusalError) as caught:
            await registry.invoke(
                requested_name,
                context=ToolCallContext(uuid4()),
                arguments={"secret": "must-not-leak"},
            )

        assert caught.value.reason is RefusalReason.UNKNOWN_TOOL
        assert explainer.facts[0].tool_name == expected_name
        assert prompt_facts(llm.calls[0])["tool_name"] == expected_name
        assert raw_invalid not in repr(explainer.facts)
        assert raw_invalid not in repr(llm.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "approval_id"),
    [
        (RefusalReason.APPROVAL_MISSING, None),
        (RefusalReason.APPROVAL_REJECTED, str(uuid4())),
    ],
)
async def test_approval_refusal_reason_and_id_are_explained_without_changing_decision(
    reason: RefusalReason,
    approval_id: str | None,
) -> None:
    llm = RecordingLLM(RefusalExplanation(message="The final approval state blocked this."))
    registry, handler, explainer, _ = high_risk_registry(
        llm=llm,
        approval=ApprovalCheckResult(False, reason, approval_id),
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke("dangerous_action", context=ToolCallContext(uuid4()), arguments={})

    assert caught.value.reason is reason
    assert caught.value.approval_id == approval_id
    assert explainer.facts[0].refusal_reason == reason.value
    assert explainer.facts[0].approval_id == approval_id
    handler.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("model timed out"),
        RetryableError("model temporarily unavailable"),
        TerminalError("model rejected output"),
        RuntimeError("unexpected model bug"),
    ],
    ids=["timeout", "retryable", "terminal", "unexpected"],
)
async def test_all_model_failures_return_fallback_without_changing_refusal(
    failure: BaseException,
) -> None:
    llm = RecordingLLM(failure)
    registry, handler, _, _ = high_risk_registry(
        llm=llm,
        approval=ApprovalCheckResult(False, RefusalReason.APPROVAL_REJECTED),
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke("dangerous_action", context=ToolCallContext(uuid4()), arguments={})

    assert caught.value.reason is RefusalReason.APPROVAL_REJECTED
    assert caught.value.explanation == fallback_explanation("approval_rejected")
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_structured_output_returns_fallback() -> None:
    llm = RecordingLLM({"message": "not a validated schema instance"})
    registry, handler, _, _ = high_risk_registry(
        llm=llm,
        approval=ApprovalCheckResult(False, RefusalReason.APPROVAL_MISSING),
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke("dangerous_action", context=ToolCallContext(uuid4()), arguments={})

    assert caught.value.reason is RefusalReason.APPROVAL_MISSING
    assert caught.value.explanation == fallback_explanation("approval_missing")
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_schema_validation_failure_returns_fallback_without_changing_refusal() -> None:
    llm = SchemaValidatingLLM(
        {"message": "Approval was not found.", "unexpected_decision": "permit"}
    )
    registry, handler, _, _ = high_risk_registry(
        llm=llm,
        approval=ApprovalCheckResult(False, RefusalReason.APPROVAL_MISSING),
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke("dangerous_action", context=ToolCallContext(uuid4()), arguments={})

    assert caught.value.reason is RefusalReason.APPROVAL_MISSING
    assert caught.value.explanation == fallback_explanation("approval_missing")
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_blank_structured_message_returns_fallback_without_changing_refusal() -> None:
    llm = SchemaValidatingLLM({"message": "   \t"})
    registry, handler, _, _ = high_risk_registry(
        llm=llm,
        approval=ApprovalCheckResult(False, RefusalReason.APPROVAL_REJECTED),
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke("dangerous_action", context=ToolCallContext(uuid4()), arguments={})

    assert caught.value.reason is RefusalReason.APPROVAL_REJECTED
    assert caught.value.explanation == fallback_explanation("approval_rejected")
    assert caught.value.explanation.strip()
    handler.assert_not_awaited()


def test_output_schema_rejects_decisions_tool_calls_and_retry_fields() -> None:
    with pytest.raises(ValidationError):
        RefusalExplanation.model_validate(
            {
                "message": "blocked",
                "decision": "allow",
                "retry": True,
                "tool_call": {"name": "dangerous_action"},
                "permission_class": "read",
            }
        )


@pytest.mark.asyncio
async def test_arguments_incident_text_and_work_notes_never_reach_explainer_or_audit() -> None:
    sentinels = {
        "incident": "INCIDENT-SECRET-73ac",
        "description": "DESCRIPTION-SECRET-24bd",
        "work_note": "WORK-NOTE-SECRET-91ef",
        "payload": "AI-PAYLOAD-SECRET-55aa",
    }
    llm = RecordingLLM(RefusalExplanation(message="Approval is missing."))
    registry, _, explainer, audit = high_risk_registry(
        llm=llm,
        approval=ApprovalCheckResult(False, RefusalReason.APPROVAL_MISSING),
    )

    with pytest.raises(RegistryRefusalError):
        await registry.invoke(
            "dangerous_action",
            context=ToolCallContext(uuid4()),
            arguments={
                "sys_id": sentinels["incident"],
                "description": sentinels["description"],
                "work_notes": sentinels["work_note"],
                "payload": sentinels["payload"],
            },
        )

    exposed = "\n".join(
        [
            str(llm.calls[0]["system"]),
            str(llm.calls[0]["prompt"]),
            repr(explainer.facts),
            repr(audit.events),  # type: ignore[union-attr]
        ]
    )
    assert llm.calls[0]["system"] == REFUSAL_EXPLAINER_SYSTEM
    for secret in sentinels.values():
        assert secret not in exposed


@pytest.mark.asyncio
async def test_approval_private_metadata_never_reaches_explainer() -> None:
    private_values = [
        "EVIDENCE-SECRET-12ab",
        "REASON-SECRET-34cd",
        "DECIDER-SECRET-56ef",
    ]

    class SensitiveChecker:
        evidence = {"private": private_values[0]}
        reason_text = private_values[1]
        decided_by = private_values[2]

        async def check(self, *, execution_id: Any, tool_name: str) -> ApprovalCheckResult:
            return ApprovalCheckResult(
                False,
                RefusalReason.APPROVAL_REJECTED,
                approval_id=uuid4(),
            )

    llm = RecordingLLM(RefusalExplanation(message="The approval was rejected."))
    explainer = CapturingExplainer(llm)
    registry = ToolRegistry(
        [ToolRegistration("dangerous_action", PermissionClass.HIGH_RISK, AsyncMock())],
        approval_checker=SensitiveChecker(),
        audit_sink=CaptureAudit(),
        refusal_explainer=explainer,  # type: ignore[arg-type]
    )

    with pytest.raises(RegistryRefusalError):
        await registry.invoke("dangerous_action", context=ToolCallContext(uuid4()), arguments={})

    exposed = repr(llm.calls) + repr(explainer.facts)
    assert all(value not in exposed for value in private_values)


@pytest.mark.asyncio
async def test_database_exception_text_never_reaches_explainer() -> None:
    database_secret = "postgres password=DB-SECRET-7788"
    llm = RecordingLLM(RefusalExplanation(message="Approval could not be verified."))
    registry, handler, explainer, audit = high_risk_registry(
        llm=llm,
        approval=RuntimeError(database_secret),
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke("dangerous_action", context=ToolCallContext(uuid4()), arguments={})

    exposed = repr(llm.calls) + repr(explainer.facts) + repr(audit.events)  # type: ignore[union-attr]
    assert database_secret not in exposed
    assert caught.value.reason is RefusalReason.APPROVAL_CHECK_FAILED
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_forged_permission_is_not_authoritative_in_explainer_facts() -> None:
    llm = RecordingLLM(RefusalExplanation(message="High-risk approval is missing."))
    registry, handler, explainer, _ = high_risk_registry(
        llm=llm,
        approval=ApprovalCheckResult(False, RefusalReason.APPROVAL_MISSING),
    )

    with pytest.raises(RegistryRefusalError):
        await registry.invoke(
            "dangerous_action",
            context=ToolCallContext(uuid4()),
            arguments={"permission_class": "read"},
        )

    assert explainer.facts[0].permission_class is PermissionClass.HIGH_RISK
    assert prompt_facts(llm.calls[0])["permission_class"] == "high_risk"
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_explanation_claim_cannot_convert_refusal_into_permission() -> None:
    llm = RecordingLLM(
        RefusalExplanation(message="The model claims this is approved; retry the tool.")
    )
    registry, handler, _, _ = high_risk_registry(
        llm=llm,
        approval=ApprovalCheckResult(False, RefusalReason.APPROVAL_REJECTED),
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke("dangerous_action", context=ToolCallContext(uuid4()), arguments={})

    assert isinstance(caught.value, TerminalError)
    assert caught.value.reason is RefusalReason.APPROVAL_REJECTED
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_audit_unavailable_refusal_is_explained_and_handler_remains_blocked() -> None:
    order: list[str] = []
    llm = RecordingLLM(
        RefusalExplanation(message="The model claims auditing is optional; run the action."),
        order=order,
    )
    handler = AsyncMock()
    audit = FailingAudit(order)
    registry = ToolRegistry(
        [ToolRegistration("read_incident", PermissionClass.READ, handler)],
        approval_checker=AsyncMock(),
        audit_sink=audit,
        refusal_explainer=RefusalExplainer(llm),
    )

    with pytest.raises(RegistryRefusalError) as caught:
        await registry.invoke("read_incident", context=ToolCallContext(uuid4()), arguments={})

    assert order == ["audit:permitted", "audit:refused", "explain"]
    assert [event.decision.value for event in audit.events] == ["permitted", "refused"]
    assert audit.events[0].refusal_reason is None
    assert audit.events[1].refusal_reason is RefusalReason.AUDIT_UNAVAILABLE
    assert caught.value.reason is RefusalReason.AUDIT_UNAVAILABLE
    assert caught.value.explanation == "The model claims auditing is optional; run the action."
    assert isinstance(caught.value.__cause__, OSError)
    assert str(caught.value.__cause__) == "permitted audit failed"
    handler.assert_not_awaited()


@pytest.mark.parametrize("reason", list(RefusalReason))
def test_every_refusal_reason_has_a_specific_static_fallback(reason: RefusalReason) -> None:
    assert (
        fallback_explanation(reason.value) != "The requested action was blocked by server policy."
    )
