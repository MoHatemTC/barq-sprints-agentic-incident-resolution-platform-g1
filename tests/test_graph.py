"""Whole-graph tests (S2.5): routing, edge conditions, checkpoint resume.

The compiled LangGraph runs end to end with mocked dependencies and LangGraph's
in-memory checkpointer. ``tests/test_checkpointer.py`` repeats the resume
scenario against the real ``workflow_state`` table.
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent import edges
from agent.graph import build_graph, run_graph
from agent.nodes import NODE_ORDER, NODES
from agent.prompts import ClassifyOutput, DiagnoseOutput
from agent.state import EventPayload
from app.workers.retry_policy import RetryableError, TerminalError
from tests.agent_support import (
    EXECUTION_ID,
    LEAVE,
    MFA,
    ORDER_P1,
    PRINTER,
    VPN,
    FakeLLM,
    FakeRetriever,
    FakeServiceNow,
    event_for,
    evidence,
    make_deps,
    vpn_answers,
)

FULL_PATH = list(NODE_ORDER)


def run(
    record: dict[str, Any],
    deps: Any,
    *,
    checkpointer: Any = None,
    attempt: int = 1,
    execution_id: str = EXECUTION_ID,
) -> dict[str, Any]:
    graph = build_graph(deps, checkpointer=checkpointer)
    return run_graph(
        graph,
        EventPayload.model_validate(event_for(record)),
        execution_id=execution_id,
        correlation_id="corr-test",
        attempt=attempt,
        deps=deps,
    )


def label(value: str) -> dict[str, Any]:
    return {"classify": ClassifyOutput(label=value, rationale="r", confidence=0.9)}  # type: ignore[arg-type]


class TestRoutes:
    def test_happy_path_visits_all_eleven_nodes_in_order(self) -> None:
        backend = FakeServiceNow()
        deps = make_deps(servicenow=backend)
        result = run(VPN, deps)
        assert result["path"] == FULL_PATH
        assert result["outcome"] == "suggested"
        assert result["suggestion"].startswith("1. Confirm the password")
        assert "[KB0001 v2 §Resolution]" in result["suggestion"]
        assert result["write_back"] == "written"
        assert len(backend.updates) == 1

    def test_high_risk_escalates_at_determine_risk_without_retrieval_or_generation(self) -> None:
        llm = FakeLLM(vpn_answers() | label("software"))
        retriever = FakeRetriever()
        backend = FakeServiceNow()
        deps = make_deps(llm=llm, retriever=retriever, servicenow=backend)

        result = run(ORDER_P1, deps)

        assert result["path"] == ["load", "validate", "classify", "determine_risk", "act"]
        assert result["outcome"] == "escalated_high_risk"
        assert retriever.calls == []
        assert llm.purposes() == ["classify"]  # no diagnose, no generate
        body = backend.updates[0][1].to_table_api_body()
        assert "x_2215032_ai_inc_0_ai_suggestion" not in body
        assert body["x_2215032_ai_inc_0_ai_human_review_required"] == "true"

    def test_no_evidence_escalates_after_retrieve(self) -> None:
        llm = FakeLLM(vpn_answers() | label("hardware"))
        retriever = FakeRetriever(hits=[evidence("KB0004", relevance=0.31)])
        result = run(PRINTER, make_deps(llm=llm, retriever=retriever))
        assert result["path"] == [
            "load",
            "validate",
            "classify",
            "determine_risk",
            "retrieve",
            "act",
        ]
        assert result["outcome"] == "escalated_no_evidence"
        assert llm.purposes() == ["classify"]

    def test_w03_leave_request_is_escalated(self) -> None:
        # Task 0 test set: "no knowledge-base article covers this — it must go to a human".
        llm = FakeLLM(vpn_answers() | label("other"))
        result = run(LEAVE, make_deps(llm=llm, retriever=FakeRetriever(hits=[])))
        assert result["outcome"] == "escalated_no_evidence"
        assert result["escalated"] is True

    def test_unmatched_evidence_escalates_after_diagnose(self) -> None:
        answers = vpn_answers()
        answers["diagnose"] = DiagnoseOutput(
            probable_cause="unclear",
            matched_article_ids=[],
            symptom_match=False,
            confidence=0.2,
            rationale="nothing fits",
        )
        llm = FakeLLM(answers)
        result = run(VPN, make_deps(llm=llm))
        assert result["path"][-2:] == ["diagnose", "act"]
        assert result["outcome"] == "escalated_no_evidence"
        assert "generate" not in llm.purposes()

    def test_low_confidence_runs_every_node_then_withholds_the_draft(self) -> None:
        backend = FakeServiceNow()
        deps = make_deps(llm=FakeLLM(vpn_answers(confidence=0.3)), servicenow=backend)
        result = run(VPN, deps)
        assert result["path"] == FULL_PATH
        assert result["outcome"] == "escalated_low_confidence"
        body = backend.updates[0][1].to_table_api_body()
        assert "x_2215032_ai_inc_0_ai_suggestion" not in body
        assert body["x_2215032_ai_inc_0_ai_confidence"] == "0.30"

    def test_ineligible_goes_straight_to_act_and_writes_nothing(self) -> None:
        backend = FakeServiceNow()
        record = {**VPN, "x_2215032_ai_inc_0_ai_human_lock": "true"}
        backend.records[VPN["sys_id"]] = record
        llm = FakeLLM(vpn_answers())
        result = run(VPN, make_deps(llm=llm, servicenow=backend))
        assert result["path"] == ["load", "validate", "act"]
        assert result["outcome"] == "skipped_ineligible"
        assert llm.calls == []
        assert backend.updates == []

    def test_elevated_risk_drafts_and_awaits_approval(self) -> None:
        llm = FakeLLM(vpn_answers() | label("access"))
        result = run(MFA, make_deps(llm=llm))
        assert result["path"] == FULL_PATH
        assert result["outcome"] == "suggested"
        assert result["processing_state"] == "awaiting_approval"

    def test_failed_gate_routes_to_act(self) -> None:
        def failing_safety(state: Any, deps: Any) -> dict[str, Any]:
            return {
                "safety": {
                    "gate": "safety_check",
                    "passed": False,
                    "implemented": True,
                    "checks": [],
                    "reason": "draft contains a credential",
                }
            }

        deps = make_deps()
        graph = build_graph(deps, nodes={"safety_check": failing_safety})
        result = run_graph(
            graph,
            EventPayload.model_validate(event_for(VPN)),
            execution_id=EXECUTION_ID,
            correlation_id="c",
            attempt=1,
            deps=deps,
        )
        assert result["path"][-2:] == ["safety_check", "act"]
        assert result["outcome"] == "escalated_blocked"

    def test_graph_is_deterministic(self) -> None:
        first = run(VPN, make_deps())
        second = run(VPN, make_deps())
        assert first == second


class TestEdgeConditions:
    """Each router, on each value it can see, including missing/garbled state."""

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            ({"eligibility": {"eligible": True, "reasons": []}}, "classify"),
            ({"eligibility": {"eligible": False, "reasons": ["x"]}}, "act"),
            ({}, "act"),
            ({"eligibility": {"eligible": "maybe"}}, "act"),
        ],
    )
    def test_after_validate(self, state: dict[str, Any], expected: str) -> None:
        assert edges.after_validate(state) == expected  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("level", "expected"),
        [("low", "retrieve"), ("elevated", "retrieve"), ("high", "act"), ("bogus", "act")],
    )
    def test_after_determine_risk(self, level: str, expected: str) -> None:
        state = {"risk": {"level": level, "reasons": [], "approval_required": False}}
        assert edges.after_determine_risk(state) == expected  # type: ignore[arg-type]
        assert edges.after_determine_risk({}) == "act"

    @pytest.mark.parametrize(("sufficient", "expected"), [(True, "diagnose"), (False, "act")])
    def test_after_retrieve(self, sufficient: bool, expected: str) -> None:
        state = {
            "retrieval": {
                "query": "q",
                "category_filter": None,
                "hits": [],
                "best_relevance": 0.9 if sufficient else 0.1,
                "threshold": 0.55,
                "sufficient": sufficient,
                "latency_ms": 1.0,
            }
        }
        assert edges.after_retrieve(state) == expected  # type: ignore[arg-type]
        assert edges.after_retrieve({}) == "act"

    @pytest.mark.parametrize(("matched", "expected"), [(["KB0001-v2"], "generate"), ([], "act")])
    def test_after_diagnose(self, matched: list[str], expected: str) -> None:
        state = {
            "diagnosis": {
                "probable_cause": "c",
                "matched_article_ids": matched,
                "symptom_match": bool(matched),
                "model_confidence": 0.5,
                "rationale": "r",
            }
        }
        assert edges.after_diagnose(state) == expected  # type: ignore[arg-type]
        assert edges.after_diagnose({}) == "act"

    @pytest.mark.parametrize("passed", [True, False])
    def test_gate_routers(self, passed: bool) -> None:
        def gate(name: str) -> dict[str, Any]:
            return {"gate": name, "passed": passed, "implemented": False, "checks": []}

        verify_unexhausted = edges.after_verify_evidence(
            {"verification": gate("verify_evidence"), "revision_count": 0}  # type: ignore[typeddict-item]
        )
        verify_exhausted = edges.after_verify_evidence(
            {"verification": gate("verify_evidence"), "revision_count": 2}  # type: ignore[typeddict-item]
        )
        safety = edges.after_safety_check({"safety": gate("safety_check")})  # type: ignore[typeddict-item]
        assert verify_unexhausted == ("safety_check" if passed else "generate")
        assert verify_exhausted == ("safety_check" if passed else "act")
        assert safety == ("confidence_check" if passed else "act")
        assert edges.after_verify_evidence({}) == "act"
        assert edges.after_safety_check({}) == "act"

    def test_edge_table_matches_the_compiled_graph(self) -> None:
        compiled = build_graph(make_deps()).get_graph()
        actual = {(e.source, e.target) for e in compiled.edges}
        documented = {(src, dst) for src, _, dst in edges.EDGE_TABLE}
        assert actual == documented

    def test_risk_is_determined_before_any_retrieval(self) -> None:
        """Every path from START to retrieve passes through determine_risk."""
        compiled = build_graph(make_deps()).get_graph()
        successors: dict[str, set[str]] = {}
        for edge in compiled.edges:
            successors.setdefault(edge.source, set()).add(edge.target)

        # Remove determine_risk and check retrieve becomes unreachable.
        seen, frontier = set(), ["__start__"]
        while frontier:
            node = frontier.pop()
            if node in seen or node == "determine_risk":
                continue
            seen.add(node)
            frontier.extend(successors.get(node, ()))
        assert "retrieve" not in seen
        assert "generate" not in seen


class TestCheckpointing:
    def test_retry_resumes_after_the_last_completed_node(self) -> None:
        saver = InMemorySaver()
        answers = vpn_answers()
        good_generate = answers["generate"]
        answers["generate"] = RetryableError("model overloaded")
        llm = FakeLLM(answers)
        backend = FakeServiceNow()
        deps = make_deps(llm=llm, servicenow=backend)

        with pytest.raises(RetryableError):
            run(VPN, deps, checkpointer=saver, attempt=1)
        assert llm.purposes() == ["classify", "diagnose", "generate"]
        assert backend.calls == ["read_incident"]

        answers["generate"] = good_generate
        result = run(VPN, deps, checkpointer=saver, attempt=2)

        assert result["resumed"] is True
        assert result["outcome"] == "suggested"
        # classify and diagnose were not paid for twice; load did not re-read.
        assert llm.purposes() == ["classify", "diagnose", "generate", "generate", "verify_evidence"]
        assert backend.calls == [
            "read_incident",
            "write_ai_fields",
            "write_execution_log",
        ]
        assert result["path"] == FULL_PATH

    def test_a_finished_execution_is_not_run_again(self) -> None:
        saver = InMemorySaver()
        backend = FakeServiceNow()
        deps = make_deps(servicenow=backend)
        first = run(VPN, deps, checkpointer=saver)
        second = run(VPN, deps, checkpointer=saver, attempt=2)
        assert first["outcome"] == second["outcome"] == "suggested"
        assert second["resumed"] is True
        assert len(backend.updates) == 1  # the write is not repeated

    def test_executions_are_isolated_by_thread(self) -> None:
        saver = InMemorySaver()
        deps = make_deps()
        run(VPN, deps, checkpointer=saver, execution_id=EXECUTION_ID)
        other = run(
            VPN, deps, checkpointer=saver, execution_id="11111111-1111-1111-1111-111111111111"
        )
        assert other["resumed"] is False

    def test_a_node_error_propagates_unchanged(self) -> None:
        answers = vpn_answers()
        answers["classify"] = TerminalError("model rejected the request")
        with pytest.raises(TerminalError, match="rejected"):
            run(VPN, make_deps(llm=FakeLLM(answers)))


def test_every_node_is_registered_in_the_graph() -> None:
    compiled = build_graph(make_deps()).get_graph()
    assert set(compiled.nodes) - {"__start__", "__end__"} == set(NODES)
