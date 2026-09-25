"""The eleven-node LangGraph state machine and its worker entry point (S2.5, FR-11).

::

    START → load → validate ─eligible→ classify → determine_risk ─low/elevated→ retrieve
                     │ineligible                        │high                  │ no evidence
                     ▼                                  ▼                      ▼
                    act ◀───────────────────────────────┴──────────────────────┘
                     ▲
    retrieve ─evidence→ diagnose ─matched→ generate → verify_evidence ─pass→ safety_check
                          │none                            │fail              │fail  │pass
                          └──────────────▶ act ◀───────────┴──────────────────┘      ▼
                                           ▲                                  confidence_check
                                           └──────────────────────────────────────────┘
    act → END

Every node is wrapped in a Langfuse span (``node.<name>``) and stamps
``current_node`` / ``path`` so each checkpoint row names the node that wrote it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent import edges
from agent.config import AGENT_VERSION
from agent.dependencies import AgentDependencies
from agent.nodes import NODE_ORDER, NODES
from agent.servicenow import HumanLockedError
from agent.state import AgentState, EventPayload, FinalOutput, Outcome, initial_state
from app.workers.retry_policy import TerminalError

GRAPH_NAME = "incident-resolution"
NodeFn = Callable[[AgentState, AgentDependencies], dict[str, Any]]

_SPAN_TYPES = {
    "diagnose": "agent",
    "generate": "agent",
    "verify_evidence": "agent",
    "retrieve": "retriever",
    "safety_check": "guardrail",
    "confidence_check": "guardrail",
    "act": "tool",
}


def _node_input(state: AgentState) -> dict[str, Any]:
    """What a node span shows as input: which sections exist, not their contents."""
    return {
        "execution_id": state.get("execution_id"),
        "path": state.get("path", []),
        "sections": sorted(k for k in state if k not in ("path", "current_node")),
    }


def instrument(
    name: str, fn: NodeFn, deps: AgentDependencies
) -> Callable[[AgentState], dict[str, Any]]:
    def run(state: AgentState) -> dict[str, Any]:
        with deps.tracer.span(
            f"node.{name}",
            as_type=_SPAN_TYPES.get(name, "span"),  # type: ignore[arg-type]
            input=_node_input(state),
            metadata={"node": name, "execution_id": state.get("execution_id")},
        ) as span:
            update = fn(state, deps)
            span.update(output=update)
        return {**update, "current_node": name, "path": [name]}

    run.__name__ = name
    return run


def build_graph(
    deps: AgentDependencies,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    nodes: dict[str, NodeFn] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the state machine. ``nodes`` lets tests replace individual nodes."""
    table = {**NODES, **(nodes or {})}
    builder: StateGraph[Any, Any, Any, Any] = StateGraph(AgentState)
    for name in NODE_ORDER:
        builder.add_node(name, cast(Any, instrument(name, table[name], deps)))

    builder.add_edge(START, "load")
    builder.add_edge("load", "validate")
    builder.add_conditional_edges(
        "validate", edges.after_validate, {"classify": "classify", "act": "act"}
    )
    builder.add_edge("classify", "determine_risk")
    builder.add_conditional_edges(
        "determine_risk", edges.after_determine_risk, {"retrieve": "retrieve", "act": "act"}
    )
    builder.add_conditional_edges(
        "retrieve", edges.after_retrieve, {"diagnose": "diagnose", "act": "act"}
    )
    builder.add_conditional_edges(
        "diagnose", edges.after_diagnose, {"generate": "generate", "act": "act"}
    )
    builder.add_edge("generate", "verify_evidence")
    builder.add_conditional_edges(
        "verify_evidence",
        edges.after_verify_evidence,
        {"safety_check": "safety_check", "generate": "generate", "act": "act"},
    )
    builder.add_conditional_edges(
        "safety_check",
        edges.after_safety_check,
        {"confidence_check": "confidence_check", "act": "act"},
    )
    builder.add_edge("confidence_check", "act")
    builder.add_edge("act", END)
    return builder.compile(checkpointer=checkpointer, name=GRAPH_NAME)


def run_graph(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    event: EventPayload,
    *,
    execution_id: str,
    correlation_id: str,
    attempt: int,
    deps: AgentDependencies,
) -> dict[str, Any]:
    """Run (or resume) one execution and return its final output.

    With a checkpointer, a later attempt resumes after the last completed node; a
    thread that already reached ``act`` returns the recorded output without running
    anything again.
    """
    config: RunnableConfig = {
        "configurable": {"thread_id": execution_id, "attempt": attempt},
        "run_name": GRAPH_NAME,
        "recursion_limit": 25,
    }
    payload: AgentState | None = initial_state(
        event,
        execution_id=execution_id,
        correlation_id=correlation_id,
        started_at=deps.clock().isoformat(),
    )
    if graph.checkpointer is not None:
        snapshot = graph.get_state(config)
        if snapshot.values:
            if not snapshot.next:
                return _result(snapshot.values, resumed=True)
            payload = None  # resume from the checkpoint

    try:
        final = graph.invoke(cast(Any, payload), config)
    except HumanLockedError as exc:  # raised by load/read paths
        raise TerminalError(str(exc)) from exc
    return _result(final, resumed=payload is None)


def _result(values: dict[str, Any], *, resumed: bool) -> dict[str, Any]:
    output = values.get("output")
    if output is None:
        raise TerminalError("graph ended without an output")
    parsed = FinalOutput.model_validate(output)
    path = values.get("path", [])
    return {
        "outcome": parsed.outcome.value,
        "summary": parsed.summary,
        "suggestion": parsed.suggestion,
        "confidence": parsed.confidence,
        "processing_state": parsed.processing_state,
        "write_back": parsed.write_back,
        "path": path,
        # Carried so the worker can record the decision on the executions summary
        # row: without it every run terminates as "completed", including a
        # high-risk escalation. workflow_state remains the authoritative history.
        "node_reached": path[-1] if path else None,
        "agent_version": AGENT_VERSION,
        "resumed": resumed,
        "escalated": parsed.outcome.value.startswith("escalated"),
        "suggested": parsed.outcome is Outcome.SUGGESTED,
    }


__all__ = ["GRAPH_NAME", "build_graph", "instrument", "run_graph"]
