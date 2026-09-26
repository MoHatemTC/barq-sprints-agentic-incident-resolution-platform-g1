"""Fakes and fixtures for the S2.5 agent tests — no network, no model, no database.

The incidents mirror the worked cases in the BARQ IT Service Operations Manual
(Edition 4.0, §7) and the W0.3 Task 0 test set: a VPN failure with a clear article
(INC0010023), a mechanical printer fault with none (INC0010047), a P1 on a Tier 1
service (INC0010052) and an identity case needing approval (INC0010064).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel

from agent.config import AgentSettings
from agent.dependencies import AgentDependencies
from agent.prompts import (
    ClassifyOutput,
    CriticOutput,
    DiagnoseOutput,
    GenerateOutput,
    InjectionClassification,
    StepOutput,
)
from agent.servicenow import AsyncRunner, IncidentGateway
from agent.state import EvidenceItem, RetrievalResult
from agent.tools import build_servicenow_tool_registry
from agent.tools.registry import ApprovalCheckResult
from app.models.execution_log import ExecutionLogCreatePayload
from app.models.incident import Incident, IncidentUpdatePayload
from app.models.knowledge import Classification
from observability.tracing import Tracer

SCOPE = "x_2215032_ai_inc_0"
FIXED_NOW = datetime(2026, 9, 8, 6, 14, tzinfo=UTC)
EXECUTION_ID = "0b6f7c1e-2f7d-4c55-9a51-6d2f4b7f3c10"

_RUNNER: AsyncRunner | None = None


def shared_runner() -> AsyncRunner:
    global _RUNNER
    if _RUNNER is None:
        _RUNNER = AsyncRunner()
    return _RUNNER


def incident_record(
    number: str,
    *,
    short: str,
    description: str,
    category: str,
    priority: str,
    impact: str,
    urgency: str,
    service: str | None,
    sys_id: str | None = None,
    state: str = "1",
    ai_enabled: str = "true",
    human_lock: str = "false",
    ai_state: str = "pending",
) -> dict[str, Any]:
    """A Table API incident record, as ServiceNow returns it (strings, scoped names)."""
    record: dict[str, Any] = {
        "sys_id": sys_id or (number.lower().replace("inc", "a") + "0" * 32)[:32],
        "number": number,
        "short_description": short,
        "description": description,
        "state": state,
        "priority": priority,
        "impact": impact,
        "urgency": urgency,
        "category": category,
        "subcategory": "",
        "active": "true",
        f"{SCOPE}_ai_enabled": ai_enabled,
        f"{SCOPE}_ai_human_lock": human_lock,
        f"{SCOPE}_ai_processing_state": ai_state,
    }
    if service is not None:
        record["business_service"] = {"display_value": service, "value": "f" * 32}
    return record


VPN = incident_record(
    "INC0010023",
    short="VPN authentication fails after password reset",
    description="Can reach the internet but the VPN client says invalid credentials since I "
    "reset my password this morning. Call me on +971 50 123 4567.",
    category="network",
    priority="3",
    impact="3",
    urgency="2",
    service="corporate-vpn",
)
PRINTER = incident_record(
    "INC0010047",
    short="Printer in meeting room 4 makes a grinding noise",
    description="Paper feed grinds and nothing comes out.",
    category="hardware",
    priority="4",
    impact="3",
    urgency="3",
    service="print-services",
)
ORDER_P1 = incident_record(
    "INC0010052",
    short="order-processing returning 500s",
    description="Monitoring alert: connection pool saturation on order-processing.",
    category="software",
    priority="1",
    impact="1",
    urgency="1",
    service="order-processing",
)
MFA = incident_record(
    "INC0010064",
    short="New phone, cannot approve MFA prompts",
    description="I replaced my phone and my authenticator is gone.",
    category="inquiry",
    priority="3",
    impact="3",
    urgency="2",
    service="identity",
)
LEAVE = incident_record(
    "INC0010071",
    short="Request: annual leave approval",
    description="I would like to take next week off.",
    category="inquiry",
    priority="4",
    impact="3",
    urgency="3",
    service=None,
)

INCIDENTS = {r["number"]: r for r in (VPN, PRINTER, ORDER_P1, MFA, LEAVE)}


def event_for(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": f"evt-{record['number']}",
        "sys_id": record["sys_id"],
        "number": record["number"],
        "event_type": "incident.created",
        "contract_version": "v1",
    }


def evidence(
    article: str = "KB0001",
    *,
    version: str = "2",
    section: str = "Resolution",
    relevance: float = 0.847,
    title: str = "VPN authentication fails after a password change",
    text: str = "1. Confirm the password changed. 2. Sign out of the VPN client. "
    "3. Clear the cached credential. 4. Reconnect.",
    chunk: int = 2,
) -> EvidenceItem:
    return EvidenceItem(
        article_id=f"{article}-v{version}",
        article_number=article,
        version=version,
        title=title,
        section=section,
        chunk_index=chunk,
        text=text,
        fused_score=0.5,
        relevance=relevance,
    )


# -- fakes ------------------------------------------------------------------------------


@dataclass
class FakeLLM:
    """Scripted model: one answer (or callable, or exception) per purpose."""

    answers: dict[str, Any] = field(default_factory=dict)
    calls: list[dict[str, str]] = field(default_factory=list)
    model_name: str = "gemini/gemini-3.5-flash"
    last_model_used: str | None = None
    purpose_models: dict[str, str] = field(default_factory=dict)

    def model_for_purpose(self, purpose: str, override: str | None = None) -> str:
        return override or self.model_name

    def structured(
        self,
        *,
        purpose: str,
        system: str,
        prompt: str,
        schema: type[Any],
        model: str | None = None,
    ) -> Any:
        selected_model = self.model_for_purpose(purpose, model)
        self.last_model_used = selected_model
        self.purpose_models[purpose] = selected_model
        self.calls.append(
            {
                "purpose": purpose,
                "system": system,
                "prompt": prompt,
                "model": selected_model,
            }
        )
        answer = self.answers[purpose]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer) and not isinstance(answer, BaseModel):
            answer = answer(prompt)
        assert isinstance(answer, schema), f"{purpose} answer is not a {schema.__name__}"
        return answer

    def purposes(self) -> list[str]:
        return [c["purpose"] for c in self.calls]


def vpn_answers(confidence: float = 0.82) -> dict[str, Any]:
    return {
        "injection_classifier": InjectionClassification(
            is_injection=False, reason="ordinary VPN incident, no manipulation attempt"
        ),
        "classify": ClassifyOutput(
            label="network", rationale="VPN auth after password reset", confidence=0.93
        ),
        "diagnose": DiagnoseOutput(
            probable_cause="The VPN client presents the cached old credential.",
            matched_article_ids=["KB0001-v2"],
            symptom_match=True,
            confidence=confidence,
            rationale="KB0001 symptom matches exactly.",
        ),
        "generate": GenerateOutput(
            steps=[
                StepOutput(
                    text="Confirm the password was changed in the last 24 hours.",
                    article_id="KB0001-v2",
                    section="Resolution",
                ),
                StepOutput(
                    text="Sign out of the VPN client completely.",
                    article_id="KB0001-v2",
                    section="Resolution",
                ),
                StepOutput(
                    text="Clear the cached VPN credential and reconnect with the new password.",
                    article_id="KB0001-v2",
                    section="Resolution",
                ),
            ]
        ),
        "verify_evidence": CriticOutput(
            passed=True,
            invalid_citations=[],
            unsupported_claims=[],
            feedback_instructions="",
        ),
    }


class FakeOpenAISDK:
    """Stands in for ``openai.OpenAI`` pointed at the LiteLLM proxy:
    ``chat.completions.with_raw_response.parse`` answers from a script keyed by
    output schema, so the real :class:`LiteLLMClient` (and its Langfuse
    generations) run without a network."""

    SCHEMA_PURPOSE = {
        "ClassifyOutput": "classify",
        "DiagnoseOutput": "diagnose",
        "GenerateOutput": "generate",
        "CriticOutput": "verify_evidence",
    }

    def __init__(
        self,
        answers: dict[str, Any],
        *,
        finish_reason: str = "stop",
        refusal: str | None = None,
        cost: str | None = "0.0016455",
    ) -> None:
        self.answers = answers
        self.finish_reason = finish_reason
        self.refusal = refusal
        self.cost = cost
        self.requests: list[dict[str, Any]] = []
        raw = SimpleNamespace(parse=self._parse)
        self.chat = SimpleNamespace(completions=SimpleNamespace(with_raw_response=raw))

    def _parse(self, **request: Any) -> Any:
        self.requests.append(request)
        val = self.answers[self.SCHEMA_PURPOSE[request["response_format"].__name__]]
        if isinstance(val, list):
            answer = val.pop(0)
        else:
            answer = val
        if isinstance(answer, BaseException):
            raise answer
        completion = SimpleNamespace(
            id="chatcmpl-test",
            model=request["model"],
            usage=SimpleNamespace(
                prompt_tokens=900,
                completion_tokens=150,
                completion_tokens_details=SimpleNamespace(reasoning_tokens=40),
                prompt_tokens_details=None,
            ),
            choices=[
                SimpleNamespace(
                    finish_reason=self.finish_reason,
                    message=SimpleNamespace(parsed=answer, refusal=self.refusal),
                )
            ],
        )
        headers = {"x-litellm-response-cost": self.cost} if self.cost else {}
        return SimpleNamespace(headers=headers, parse=lambda: completion)


def sdk_llm(tracer: Tracer, answers: dict[str, Any] | None = None) -> Any:
    from agent.llm import LiteLLMClient

    return LiteLLMClient(
        AgentSettings(_env_file=None),
        tracer,
        client=FakeOpenAISDK(answers or vpn_answers()),
    )


@dataclass
class FakeRetriever:
    hits: list[EvidenceItem] = field(default_factory=lambda: [evidence()])
    calls: list[dict[str, Any]] = field(default_factory=list)
    error: BaseException | None = None

    def search(
        self,
        query: str,
        *,
        classification: Classification,
        top_k: int,
        threshold: float,
        incident_category: str | None = None,
    ) -> RetrievalResult:
        self.calls.append(
            {
                "query": query,
                "classification": classification,
                "top_k": top_k,
                "incident_category": incident_category,
            }
        )
        if self.error is not None:
            raise self.error
        best = max((h.relevance for h in self.hits), default=0.0)
        return RetrievalResult(
            query=query,
            category_filter="network",
            hits=self.hits[:top_k],
            best_relevance=best,
            threshold=threshold,
            sufficient=bool(self.hits) and best >= threshold,
            latency_ms=1.0,
        )


class FakeServiceNow:
    """Async stand-in for ``ServiceNowClient`` over an in-memory incident table."""

    def __init__(self, records: dict[str, dict[str, Any]] | None = None) -> None:
        self.records = {r["sys_id"]: dict(r) for r in (records or INCIDENTS).values()}
        self.updates: list[tuple[str, IncidentUpdatePayload]] = []
        self.notes: list[tuple[str, str]] = []
        self.execution_logs: list[ExecutionLogCreatePayload] = []
        self.calls: list[str] = []
        self.read_error: BaseException | None = None
        self.write_error: BaseException | None = None
        self.on_update: Callable[[str], None] | None = None

    async def get_incident(self, sys_id: str) -> Incident:
        self.calls.append("read_incident")
        if self.read_error is not None:
            raise self.read_error
        return Incident.model_validate(self.records[sys_id])

    async def update_incident(self, sys_id: str, payload: IncidentUpdatePayload) -> Incident:
        self.calls.append("write_ai_fields")
        if self.on_update is not None:
            self.on_update(sys_id)
        if self.write_error is not None:
            raise self.write_error
        self.updates.append((sys_id, payload))
        self.records[sys_id].update(payload.to_table_api_body())
        return Incident.model_validate(self.records[sys_id])

    async def add_work_note(self, sys_id: str, note: str) -> Incident:
        self.calls.append("write_work_note")
        self.notes.append((sys_id, note))
        return Incident.model_validate(self.records[sys_id])

    async def write_execution_log(self, payload: ExecutionLogCreatePayload) -> None:
        self.calls.append("write_execution_log")
        self.execution_logs.append(payload)


class NoHighRiskApprovalChecker:
    async def check(self, *, execution_id: Any, tool_name: str) -> ApprovalCheckResult:
        raise AssertionError("the graph's registered ServiceNow tools are not high-risk")


def make_deps(
    *,
    llm: FakeLLM | None = None,
    retriever: FakeRetriever | None = None,
    servicenow: FakeServiceNow | None = None,
    tracer: Tracer | None = None,
    **settings: Any,
) -> AgentDependencies:
    tracer = tracer or Tracer(None)
    backend = servicenow or FakeServiceNow()
    gateway = IncidentGateway(lambda: backend, tracer, runner=shared_runner())
    return AgentDependencies(
        settings=AgentSettings(_env_file=None, agent_checkpointer_backend="memory", **settings),
        llm=llm or FakeLLM(vpn_answers()),
        retriever=retriever or FakeRetriever(),
        tools=build_servicenow_tool_registry(
            gateway,
            approval_checker=NoHighRiskApprovalChecker(),
        ),
        tracer=tracer,
        clock=lambda: FIXED_NOW,
    )
