#!/usr/bin/env python3
"""Run the S2.5 graph once against real services and print the outcome and trace URL.

The graph runs inside the same ``worker.pickup`` span and trace attributes the Celery
task uses, with the real Gemini client (LiteLLM), the real Qdrant retriever and the
real Langfuse tracer from ``.env``.

ServiceNow is either live or in-memory:

    # live: reads (and, unless --dry-run, writes) a real incident as the integration user
    uv run python scripts/run_agent_live.py --number INC0010023

    # in-memory: the BARQ manual's worked incidents, nothing touches ServiceNow
    uv run python scripts/run_agent_live.py --scenario vpn
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from agent.config import get_agent_settings  # noqa: E402
from agent.dependencies import AgentDependencies  # noqa: E402
from agent.graph import build_graph, run_graph  # noqa: E402
from agent.llm import get_llm  # noqa: E402
from agent.retrieval import build_default_retriever  # noqa: E402
from agent.servicenow import IncidentGateway, build_servicenow_backend  # noqa: E402
from agent.state import EventPayload  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from observability.tracing import get_tracer, trace_id_for  # noqa: E402

SCENARIOS = {
    "vpn": "INC0010023",
    "printer": "INC0010047",
    "p1": "INC0010052",
    "mfa": "INC0010064",
    "leave": "INC0010071",
}


def find_incident(number: str) -> dict[str, Any]:
    import asyncio

    from app.clients.servicenow_client import ServiceNowClient

    async def lookup() -> dict[str, Any]:
        async with ServiceNowClient(get_settings()) as client:
            incident = await client.find_incident_by_number(number)
            if incident is None:
                raise SystemExit(f"{number} not found")
            return {"sys_id": incident.sys_id, "number": incident.number}

    return asyncio.run(lookup())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--number", help="live ServiceNow incident number")
    target.add_argument("--scenario", choices=sorted(SCENARIOS), help="in-memory manual case")
    parser.add_argument("--dry-run", action="store_true", help="never write to ServiceNow")
    args = parser.parse_args()

    settings = get_agent_settings()
    if args.dry_run:
        settings = settings.model_copy(update={"agent_write_back_enabled": False})
    tracer = get_tracer()

    if args.scenario:
        from tests.agent_support import INCIDENTS, FakeServiceNow

        record = INCIDENTS[SCENARIOS[args.scenario]]
        backend: Any = FakeServiceNow()
        gateway = IncidentGateway(lambda: backend, tracer)
        identity = {"sys_id": record["sys_id"], "number": record["number"]}
    else:
        gateway = IncidentGateway(build_servicenow_backend, tracer)
        identity = find_incident(args.number)

    deps = AgentDependencies(
        settings=settings,
        llm=get_llm(),
        retriever=build_default_retriever(),
        servicenow=gateway,
        tracer=tracer,
    )
    event = EventPayload(
        event_id=f"evt-live-{uuid.uuid4().hex[:12]}",
        sys_id=identity["sys_id"],
        number=identity["number"],
    )
    correlation_id = f"live-{uuid.uuid4()}"
    execution_id = str(uuid.uuid4())
    graph = build_graph(deps, checkpointer=InMemorySaver())
    with (
        tracer.span(
            "worker.pickup",
            correlation_id=correlation_id,
            as_type="agent",
            input={"event_id": event.event_id, "attempt": 1},
            metadata={
                "execution_id": execution_id,
                "incident_number": event.number,
                "mode": "live-script",
            },
        ) as span,
        tracer.trace_attributes(
            correlation_id=correlation_id, incident_number=event.number, execution_id=execution_id
        ),
    ):
        result = run_graph(
            graph,
            event,
            execution_id=execution_id,
            correlation_id=correlation_id,
            attempt=1,
            deps=deps,
        )
        span.update(output=result)
    tracer.flush()

    print(json.dumps(result, indent=2))
    print("servicenow calls:", gateway.calls)
    print("trace id:", trace_id_for(correlation_id))
    print("trace url:", tracer.trace_url(correlation_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
