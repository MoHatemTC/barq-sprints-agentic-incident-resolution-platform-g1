#!/usr/bin/env python3
"""Execute seeded incident runs through the real compiled graph (Step 5.3).

Executes two realistic incident fixtures against live services:
- Real Gemini models via LiteLLM (gemini-3.5-flash / gemini-3.8-flash)
- Real Qdrant vector retrieval (incident_knowledge_base)
- Real PostgreSQL workflow_state checkpointer
- Real Langfuse tracing and span observation

Fixtures:
1. Clean Pass: INC0010023 (VPN authentication failure matching KB0001).
   Passes Critic verification on attempt 0 -> safety_check -> confidence_check -> act(SUGGESTED).
2. Genuine Rejection & Correction Cycle: INC0010042 (Outlook disconnected matching KB0002).
   Initial resolution proposes ungrounded Exchange server reboot;
   Critic rejects on attempt 0 with feedback instructions;
   Resolution Agent revises into grounded procedure;
   Critic passes on attempt 1 -> act(SUGGESTED).
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.checkpointer import WorkflowStateSaver  # noqa: E402
from agent.config import get_agent_settings  # noqa: E402
from agent.dependencies import AgentDependencies  # noqa: E402
from agent.graph import build_graph, run_graph  # noqa: E402
from agent.llm import get_llm  # noqa: E402
from agent.prompts import (  # noqa: E402
    GenerateOutput,
    StepOutput,
)
from agent.retrieval import build_default_retriever  # noqa: E402
from agent.servicenow import IncidentGateway  # noqa: E402
from agent.state import EventPayload  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.models import Event, Execution, ExecutionNodeState  # noqa: E402
from app.workers.sync_engine import (  # noqa: E402
    build_sync_database_url,
    create_sync_engine,
    create_sync_session_factory,
)
from observability.tracing import get_tracer, trace_id_for  # noqa: E402
from tests.agent_support import FakeServiceNow, incident_record  # noqa: E402

# Fixture 1: Clean Pass Incident (VPN)
VPN_FIXTURE = incident_record(
    "INC0010023",
    short="VPN authentication fails after password reset",
    description=(
        "Can reach the internet but the VPN client says invalid credentials "
        "since I reset my password this morning."
    ),
    category="network",
    priority="3",
    impact="3",
    urgency="2",
    service="corporate-vpn",
)

# Fixture 2: Rejection & Correction Incident (Outlook)
OUTLOOK_FIXTURE = incident_record(
    "INC0010042",
    short="Outlook shows Disconnected and no mail delivered",
    description=(
        "Outlook client displays Disconnected for user. Need procedure to troubleshoot and resolve."
    ),
    category="software",
    priority="3",
    impact="3",
    urgency="2",
    service="exchange-email",
)


def seed_postgres_execution(engine: Any, record: dict[str, Any]) -> tuple[UUID, str]:
    """Create parent Event and Execution rows in PostgreSQL to satisfy foreign key constraints."""
    execution_uuid = uuid.uuid4()
    event_id = f"evt-seeded-{uuid.uuid4().hex[:12]}"
    with Session(engine) as session, session.begin():
        event = Event(
            event_id=event_id,
            incident_sys_id=record["sys_id"],
            incident_number=record["number"],
            event_type="incident.created",
            contract_version="v1",
        )
        session.add(event)
        session.flush()

        execution = Execution(
            execution_id=execution_uuid,
            event_record_id=event.id,
            incident_sys_id=record["sys_id"],
            status="running",
        )
        session.add(execution)
        session.flush()

    return execution_uuid, event_id


def query_workflow_state_rows(engine: Any, execution_id: UUID) -> list[dict[str, Any]]:
    """Query workflow_state checkpointer records written by the graph execution."""
    with Session(engine) as session:
        records = session.scalars(
            select(ExecutionNodeState)
            .where(ExecutionNodeState.execution_id == execution_id)
            .order_by(ExecutionNodeState.sequence_number)
        ).all()
        return [
            {
                "sequence": r.sequence_number,
                "node": r.node_name,
                "attempt": r.attempt,
                "status": r.status,
                "decision": r.decision,
            }
            for r in records
        ]


class RevisionHookLLM:
    """Wrapper around the real LLM that injects an ungrounded step on the first generation

    to trigger a genuine critique from the real Critic Agent, while leaving all subsequent
    calls (Diagnostic, Critic verification, and revised Resolution generation) completely live.
    """

    def __init__(self, real_llm: Any, ungrounded_first_draft: GenerateOutput) -> None:
        self.real_llm = real_llm
        self.ungrounded_first_draft = ungrounded_first_draft
        self.generate_count = 0

    def structured(self, *, purpose: str, system: str, prompt: str, schema: type[Any]) -> Any:
        if purpose == "generate":
            self.generate_count += 1
            if self.generate_count == 1:
                # First attempt: deliver candidate draft containing the ungrounded reboot claim
                return self.ungrounded_first_draft
        # Live model calls for diagnose, verify_evidence (attempt 0),
        # generate (revision), and verify_evidence (attempt 1)
        return self.real_llm.structured(
            purpose=purpose,
            system=system,
            prompt=prompt,
            schema=schema,
        )

    @property
    def model_name(self) -> str:
        return getattr(self.real_llm, "model_name", "gemini/gemini-3.5-flash")

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real_llm, name)


def run_clean_pass(engine: Any, session_factory: Any) -> dict[str, Any]:
    print("\n" + "=" * 70)
    print("RUN 1: CLEAN PASS (Seeded Incident: INC0010023 - VPN Authentication)")
    print("=" * 70)

    record = VPN_FIXTURE
    execution_id, event_id = seed_postgres_execution(engine, record)
    correlation_id = f"seeded-clean-{uuid.uuid4().hex[:8]}"

    tracer = get_tracer()
    real_llm = get_llm()
    retriever = build_default_retriever()
    saver = WorkflowStateSaver(session_factory)
    backend = FakeServiceNow({record["number"]: record})
    gateway = IncidentGateway(lambda: backend, tracer)

    deps = AgentDependencies(
        settings=get_agent_settings(),
        llm=real_llm,
        retriever=retriever,
        servicenow=gateway,
        tracer=tracer,
    )
    graph = build_graph(deps, checkpointer=saver)
    event = EventPayload(
        event_id=event_id,
        sys_id=record["sys_id"],
        number=record["number"],
    )

    t_start = time.perf_counter()
    with (
        tracer.span(
            "worker.pickup",
            correlation_id=correlation_id,
            as_type="agent",
            input={"event_id": event_id, "attempt": 1},
            metadata={
                "execution_id": str(execution_id),
                "incident_number": record["number"],
                "run_type": "seeded_clean_pass",
            },
        ) as span,
        tracer.trace_attributes(
            correlation_id=correlation_id,
            incident_number=record["number"],
            execution_id=str(execution_id),
        ),
    ):
        result = run_graph(
            graph,
            event,
            execution_id=str(execution_id),
            correlation_id=correlation_id,
            attempt=1,
            deps=deps,
        )
        span.update(output=result)
    duration_s = round(time.perf_counter() - t_start, 2)
    tracer.flush()

    db_rows = query_workflow_state_rows(engine, execution_id)
    trace_url = tracer.trace_url(correlation_id)

    print(f"Outcome: {result['outcome']}")
    print(f"Execution Path: {' -> '.join(result['path'])}")
    print(f"Duration: {duration_s}s")
    print(f"PostgreSQL Execution ID: {execution_id}")
    print(f"PostgreSQL workflow_state rows written: {len(db_rows)}")
    print(f"Langfuse Trace ID: {trace_id_for(correlation_id)}")
    print(f"Langfuse Trace URL: {trace_url}")
    print("\nResolution Suggestion:")
    print(result.get("suggestion", "None"))
    return {
        "scenario": "clean_pass",
        "result": result,
        "db_rows": db_rows,
        "trace_url": trace_url,
        "correlation_id": correlation_id,
        "duration_seconds": duration_s,
    }


def run_rejection_and_correction(engine: Any, session_factory: Any) -> dict[str, Any]:
    print("\n" + "=" * 70)
    print("RUN 2: REJECTION & CORRECTION CYCLE (Seeded Incident: INC0010042 - Outlook)")
    print("=" * 70)

    record = OUTLOOK_FIXTURE
    execution_id, event_id = seed_postgres_execution(engine, record)
    correlation_id = f"seeded-correction-{uuid.uuid4().hex[:8]}"

    # Ungrounded initial candidate step to be scrutinized by the real Critic Agent
    ungrounded_candidate = GenerateOutput(
        steps=[
            StepOutput(
                text="Reboot the primary Exchange server cluster to force re-connection.",
                article_id="KB0002-v3.0",
                section="Resolution",
            ),
            StepOutput(
                text="Check whether webmail works for this user in their browser.",
                article_id="KB0002-v3.0",
                section="Resolution",
            ),
        ]
    )

    tracer = get_tracer()
    real_llm = get_llm()
    hooked_llm = RevisionHookLLM(real_llm, ungrounded_first_draft=ungrounded_candidate)
    retriever = build_default_retriever()
    saver = WorkflowStateSaver(session_factory)
    backend = FakeServiceNow({record["number"]: record})
    gateway = IncidentGateway(lambda: backend, tracer)

    deps = AgentDependencies(
        settings=get_agent_settings(),
        llm=hooked_llm,  # type: ignore[arg-type]
        retriever=retriever,
        servicenow=gateway,
        tracer=tracer,
    )
    graph = build_graph(deps, checkpointer=saver)
    event = EventPayload(
        event_id=event_id,
        sys_id=record["sys_id"],
        number=record["number"],
    )

    t_start = time.perf_counter()
    with (
        tracer.span(
            "worker.pickup",
            correlation_id=correlation_id,
            as_type="agent",
            input={"event_id": event_id, "attempt": 1},
            metadata={
                "execution_id": str(execution_id),
                "incident_number": record["number"],
                "run_type": "seeded_correction_cycle",
            },
        ) as span,
        tracer.trace_attributes(
            correlation_id=correlation_id,
            incident_number=record["number"],
            execution_id=str(execution_id),
        ),
    ):
        result = run_graph(
            graph,
            event,
            execution_id=str(execution_id),
            correlation_id=correlation_id,
            attempt=1,
            deps=deps,
        )
        span.update(output=result)
    duration_s = round(time.perf_counter() - t_start, 2)
    tracer.flush()

    db_rows = query_workflow_state_rows(engine, execution_id)
    trace_url = tracer.trace_url(correlation_id)

    print(f"Outcome: {result['outcome']}")
    print(f"Execution Path: {' -> '.join(result['path'])}")
    print(f"Duration: {duration_s}s")
    print(f"PostgreSQL Execution ID: {execution_id}")
    print(f"PostgreSQL workflow_state rows written: {len(db_rows)}")
    print(f"Langfuse Trace ID: {trace_id_for(correlation_id)}")
    print(f"Langfuse Trace URL: {trace_url}")
    print("\nFinal Corrected Suggestion:")
    print(result.get("suggestion", "None"))
    return {
        "scenario": "rejection_and_correction",
        "result": result,
        "db_rows": db_rows,
        "trace_url": trace_url,
        "correlation_id": correlation_id,
        "duration_seconds": duration_s,
    }


def main() -> int:
    settings = get_settings()
    engine = create_sync_engine(build_sync_database_url(settings))
    session_factory = create_sync_session_factory(engine)

    clean_res = run_clean_pass(engine, session_factory)
    corr_res = run_rejection_and_correction(engine, session_factory)

    # Save summary report for Step 5.5 reference
    summary = {
        "clean_pass": {
            "incident": "INC0010023",
            "outcome": clean_res["result"]["outcome"],
            "path": clean_res["result"]["path"],
            "trace_url": clean_res["trace_url"],
            "db_row_count": len(clean_res["db_rows"]),
            "duration_seconds": clean_res["duration_seconds"],
        },
        "correction_cycle": {
            "incident": "INC0010042",
            "outcome": corr_res["result"]["outcome"],
            "path": corr_res["result"]["path"],
            "trace_url": corr_res["trace_url"],
            "db_row_count": len(corr_res["db_rows"]),
            "duration_seconds": corr_res["duration_seconds"],
        },
    }
    out_file = Path("docs/seeded_incident_runs_summary.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n" + "=" * 70)
    print("ALL SEEDED RUNS COMPLETE! Summary saved to docs/seeded_incident_runs_summary.json")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
