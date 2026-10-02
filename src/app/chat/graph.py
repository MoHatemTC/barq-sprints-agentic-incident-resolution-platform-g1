"""The chat LangGraph: screen → route → retrieve → answer → verify → persist.

Bounded by construction: exactly one retrieval attempt per turn, one
answer-repair attempt after failed citation verification, no tool loop. The
graph is compiled without a checkpointer — each turn is a fresh invocation
whose context the service rehydrates from PostgreSQL.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.chat import nodes
from app.chat.nodes import ChatGraphDeps
from app.chat.state import ChatState


def _after_screen(state: ChatState) -> str:
    return "blocked" if state.get("screening", {}).get("blocked") else "ok"


def _after_route(state: ChatState) -> str:
    return "knowledge" if state.get("route") == "knowledge" else "unavailable"


def _after_verify(state: ChatState) -> str:
    return "repair" if state.get("repair_pending") else "done"


def build_chat_graph(deps: ChatGraphDeps) -> CompiledStateGraph:
    builder = StateGraph(ChatState)
    builder.add_node("screen_input", lambda state: nodes.screen_input(state, deps))
    builder.add_node("route", lambda state: nodes.route(state, deps))
    builder.add_node("retrieve_knowledge", lambda state: nodes.retrieve_knowledge(state, deps))
    builder.add_node("generate_answer", lambda state: nodes.generate_answer(state, deps))
    builder.add_node("verify_answer", lambda state: nodes.verify_answer(state, deps))
    builder.add_node("unavailable_notice", lambda state: nodes.unavailable_notice(state, deps))
    builder.add_node("persist_turn", lambda state: nodes.persist_turn(state, deps))

    builder.add_edge(START, "screen_input")
    builder.add_conditional_edges(
        "screen_input", _after_screen, {"blocked": "persist_turn", "ok": "route"}
    )
    builder.add_conditional_edges(
        "route", _after_route, {"knowledge": "retrieve_knowledge", "unavailable": "unavailable_notice"}
    )
    builder.add_edge("retrieve_knowledge", "generate_answer")
    builder.add_edge("generate_answer", "verify_answer")
    builder.add_conditional_edges(
        "verify_answer", _after_verify, {"repair": "generate_answer", "done": "persist_turn"}
    )
    builder.add_edge("unavailable_notice", "persist_turn")
    builder.add_edge("persist_turn", END)
    return builder.compile()


__all__ = ["build_chat_graph"]
