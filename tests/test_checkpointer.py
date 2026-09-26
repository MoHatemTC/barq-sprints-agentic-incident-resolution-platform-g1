"""The ``workflow_state`` checkpointer (S2.5 ↔ S2.2).

The unit tests cover the row mapping. The integration tests run the compiled graph
against real PostgreSQL (``pytest -m integration``, docker services up) and prove
that a retried delivery resumes from the table without repeating work.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.checkpointer import (
    INPUT_NODE,
    START_NODE,
    WorkflowStateSaver,
    node_name_for,
    summarize,
)
from agent.graph import build_graph, run_graph
from agent.nodes import NODE_ORDER
from agent.prompts import ClassifyOutput
from agent.state import EventPayload
from app.workers.retry_policy import RetryableError
from tests.agent_support import (
    ORDER_P1,
    VPN,
    FakeLLM,
    FakeServiceNow,
    event_for,
    make_deps,
    vpn_answers,
)

# -- row mapping ------------------------------------------------------------------------------


class TestRowMapping:
    def test_node_names(self) -> None:
        assert node_name_for({}, {"source": "input", "step": -1}) == INPUT_NODE
        assert node_name_for({"current_node": INPUT_NODE}, {"source": "loop"}) == START_NODE
        assert node_name_for({"current_node": "retrieve"}, {"source": "loop"}) == "retrieve"

    def test_bootstrap_rows_are_started(self) -> None:
        assert summarize(INPUT_NODE, {}) == ("started", None, [])

    def test_retrieve_row_carries_citations_not_text(self) -> None:
        values = {
            "retrieval": {
                "query": "q",
                "sufficient": True,
                "best_relevance": 0.8,
                "hits": [
                    {
                        "article_id": "KB0001-v2.0",
                        "section": "Resolution",
                        "relevance": 0.8,
                        "fused_score": 0.5,
                        "text": "long chunk text",
                    }
                ],
            }
        }
        status, decision, evidence = summarize("retrieve", values)
        assert status == "succeeded"
        assert decision == {"query": "q", "sufficient": True, "best_relevance": 0.8, "hit_count": 1}
        assert evidence == [
            {
                "article_id": "KB0001-v2.0",
                "section": "Resolution",
                "relevance": 0.8,
                "fused_score": 0.5,
            }
        ]

    @pytest.mark.parametrize(
        ("outcome", "status"),
        [
            ("suggested", "awaiting_approval"),
            ("escalated_high_risk", "blocked"),
            ("escalated_no_evidence", "blocked"),
            ("skipped_ineligible", "skipped"),
            ("skipped_human_lock", "skipped"),
        ],
    )
    def test_act_row_status(self, outcome: str, status: str) -> None:
        values = {"output": {"outcome": outcome, "processing_state": "awaiting_approval"}}
        assert summarize("act", values)[0] == status

    def test_load_row_keeps_no_free_text(self) -> None:
        values = {"incident": {"number": "INC1", "description": "secret stuff", "priority": 3}}
        _, decision, _ = summarize("load", values)
        assert decision is not None
        assert "description" not in decision
        assert decision["priority"] == 3

    def test_routing_nodes_record_their_section(self) -> None:
        risk = {"level": "high", "reasons": ["P1"], "approval_required": True}
        assert summarize("determine_risk", {"risk": risk}) == ("succeeded", risk, [])


# -- PostgreSQL ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pg_engine():
    from app.core.config import get_settings
    from app.db.models import Base
    from app.workers.sync_engine import build_sync_database_url, create_sync_engine

    engine = create_sync_engine(build_sync_database_url(get_settings()))
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def saver(pg_engine) -> WorkflowStateSaver:
    from app.workers.sync_engine import create_sync_session_factory

    return WorkflowStateSaver(create_sync_session_factory(pg_engine))


def seed_execution(engine, record: dict[str, Any]) -> UUID:
    from app.db.models import Event, Execution

    with Session(engine) as session, session.begin():
        event = Event(
            event_id=f"evt-{uuid4().hex[:20]}",
            incident_sys_id=record["sys_id"],
            incident_number=record["number"],
            event_type="incident.created",
            contract_version="v1",
        )
        session.add(event)
        session.flush()
        execution = Execution(
            event_record_id=event.id, incident_sys_id=record["sys_id"], status="running"
        )
        session.add(execution)
        session.flush()
        return execution.execution_id


def rows(engine, execution_id: UUID) -> list[Any]:
    from app.db.models import ExecutionNodeState

    with Session(engine) as session:
        return list(
            session.scalars(
                select(ExecutionNodeState)
                .where(ExecutionNodeState.execution_id == execution_id)
                .order_by(ExecutionNodeState.sequence_number)
            )
        )


def run(
    record: dict[str, Any],
    deps: Any,
    saver: WorkflowStateSaver,
    execution_id: UUID,
    attempt: int,
    resume: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return run_graph(
        build_graph(deps, checkpointer=saver),
        EventPayload.model_validate(event_for(record)),
        execution_id=str(execution_id),
        correlation_id="corr-pg",
        attempt=attempt,
        deps=deps,
        resume=resume,
    )


@pytest.mark.integration
class TestWorkflowStateTable:
    def test_one_row_per_node_in_order(self, pg_engine, saver) -> None:
        execution_id = seed_execution(pg_engine, VPN)
        result = run(VPN, make_deps(), saver, execution_id, attempt=1)
        assert result["outcome"] == "suggested"

        stored = rows(pg_engine, execution_id)
        assert [r.node_name for r in stored] == [INPUT_NODE, START_NODE, *NODE_ORDER]
        assert [r.sequence_number for r in stored] == list(range(1, len(stored) + 1))
        assert {r.attempt for r in stored} == {1}
        assert stored[-1].status == "awaiting_approval"
        assert stored[-1].decision["outcome"] == "suggested"
        retrieve_row = next(r for r in stored if r.node_name == "retrieve")
        assert retrieve_row.evidence[0]["article_id"] == "KB0001-v2"
        risk_row = next(r for r in stored if r.node_name == "determine_risk")
        assert risk_row.decision["level"] == "low"
        assert all(r.ended_at >= r.started_at for r in stored)

        from app.db.models import Execution

        with Session(pg_engine) as session:
            assert session.get(Execution, execution_id).node_reached == "act"

    def test_retry_resumes_from_the_table(self, pg_engine, saver) -> None:
        execution_id = seed_execution(pg_engine, VPN)
        answers = vpn_answers()
        good = answers["generate"]
        answers["generate"] = RetryableError("overloaded")
        llm = FakeLLM(answers)
        backend = FakeServiceNow()
        deps = make_deps(llm=llm, servicenow=backend)

        with pytest.raises(RetryableError):
            run(VPN, deps, saver, execution_id, attempt=1)
        first = rows(pg_engine, execution_id)
        assert first[-1].node_name == "diagnose"

        answers["generate"] = good
        # A new saver instance: nothing survives in memory between deliveries.
        from app.workers.sync_engine import create_sync_session_factory

        fresh = WorkflowStateSaver(create_sync_session_factory(pg_engine))
        result = run(VPN, deps, fresh, execution_id, attempt=2)

        assert result["resumed"] is True
        assert result["outcome"] == "suggested"
        # generate ran twice (the failed attempt and the resume); verify_evidence
        # runs once, after the draft the resume produced.
        assert llm.purposes() == ["classify", "diagnose", "generate", "generate", "verify_evidence"]
        # write_execution_log is S3.1's multi-agent audit write-back (#156).
        assert backend.calls == ["read_incident", "write_ai_fields", "write_execution_log"]
        stored = rows(pg_engine, execution_id)
        by_attempt = {(r.node_name, r.attempt) for r in stored}
        assert ("classify", 1) in by_attempt and ("classify", 2) not in by_attempt
        assert ("generate", 2) in by_attempt and ("act", 2) in by_attempt

        # A redelivery after completion changes nothing.
        again = run(VPN, deps, fresh, execution_id, attempt=3)
        assert again["outcome"] == "suggested"
        assert len(backend.updates) == 1
        assert len(rows(pg_engine, execution_id)) == len(stored)

    def test_high_risk_path_rows(self, pg_engine, saver) -> None:
        execution_id = seed_execution(pg_engine, ORDER_P1)
        answers = vpn_answers() | {
            "classify": ClassifyOutput(label="software", rationale="r", confidence=0.9)
        }
        backend = FakeServiceNow()
        deps = make_deps(llm=FakeLLM(answers), servicenow=backend)
        paused = run(ORDER_P1, deps, saver, execution_id, attempt=1)
        assert paused["paused"] is True
        assert backend.updates == []

        stored = rows(pg_engine, execution_id)
        assert [r.node_name for r in stored][2:] == [
            "load",
            "validate",
            "classify",
            "determine_risk",
            "act",
        ]
        # act parked inside interrupt(), so it has no completed checkpoint of its
        # own; record_pause() writes the row a reader of workflow_state needs.
        assert stored[-1].status == "awaiting_approval"
        assert stored[-1].decision["processing_state"] == "awaiting_approval"
        assert (
            next(r for r in stored if r.node_name == "determine_risk").decision["level"] == "high"
        )

        resumed = run(
            ORDER_P1,
            deps,
            saver,
            execution_id,
            1,
            resume={"decision": "approved", "decided_by": "lead_ops", "reason": "change window"},
        )
        assert resumed["paused"] is False
        assert resumed["resumed"] is True
        assert len(backend.updates) == 1

        final = rows(pg_engine, execution_id)
        assert [r.node_name for r in final] == [r.node_name for r in stored]
        assert final[-1].status == "blocked"
        assert final[-1].decision["outcome"] == "escalated_high_risk"

    def test_audit_rows_are_not_read_as_checkpoints(self, pg_engine, saver) -> None:
        """S3.4's audit store shares ``workflow_state``; its rows are not checkpoints.

        ``act`` saves the interrupt *before* it parks, so on a paused thread the
        audit row is the newest row — reading it as a checkpoint raised
        ``KeyError('checkpoint')`` and failed the run instead of pausing it.
        Every unit test uses the in-memory audit store, so only this pairing of
        the Postgres audit store with the Postgres checkpointer sees it.
        """
        from agent.audit_store import PostgresGraphAuditStore
        from app.workers.sync_engine import create_sync_session_factory

        execution_id = seed_execution(pg_engine, ORDER_P1)
        answers = vpn_answers() | {
            "classify": ClassifyOutput(label="software", rationale="r", confidence=0.9)
        }
        backend = FakeServiceNow()
        deps = make_deps(llm=FakeLLM(answers), servicenow=backend)
        deps.audit = PostgresGraphAuditStore(create_sync_session_factory(pg_engine))

        paused = run(ORDER_P1, deps, saver, execution_id, attempt=1)
        assert paused["paused"] is True
        assert backend.updates == []

        stored = rows(pg_engine, execution_id)
        assert "hitl.interrupt" in [r.node_name for r in stored]

        # Whether the audit row lands before or after the graph's own rows depends
        # on LangGraph's write timing, so force the case that broke: the audit row
        # re-upserted last, then a plain read of the thread.
        interrupt = deps.audit.get_interrupt(str(execution_id))
        assert interrupt is not None
        deps.audit.save_interrupt(str(execution_id), interrupt)
        assert rows(pg_engine, execution_id)[-1].node_name == "hitl.interrupt"
        config = {"configurable": {"thread_id": str(execution_id), "attempt": 1}}
        assert saver.get_tuple(config) is not None
        assert saver.get_tuple(config).checkpoint["id"] is not None  # type: ignore[union-attr]

        resumed = run(
            ORDER_P1,
            deps,
            saver,
            execution_id,
            1,
            resume={"decision": "approved", "decided_by": "lead_ops", "reason": "change window"},
        )
        assert resumed["paused"] is False
        assert resumed["resumed"] is True
        assert len(backend.updates) == 1
        assert deps.audit.get_interrupt(str(execution_id)) is not None

    def test_same_node_same_attempt_replaces_the_row(self, pg_engine, saver) -> None:
        execution_id = seed_execution(pg_engine, VPN)
        graph = build_graph(make_deps(), checkpointer=saver)
        config = {"configurable": {"thread_id": str(execution_id), "attempt": 1}}
        graph.invoke(
            {
                "execution_id": str(execution_id),
                "correlation_id": "c",
                "event": event_for(VPN),
                "started_at": dt.datetime.now(dt.UTC).isoformat(),
                "current_node": INPUT_NODE,
                "path": [],
            },
            config,  # type: ignore[arg-type]
        )
        before = rows(pg_engine, execution_id)
        latest = saver.get_tuple(config)  # type: ignore[arg-type]
        assert latest is not None
        # Re-put the final checkpoint as if a hard-killed worker re-ran act.
        saver.put(
            {"configurable": {**latest.config["configurable"], "attempt": 1}},
            latest.checkpoint,
            latest.metadata,
            {},
        )
        after = rows(pg_engine, execution_id)
        assert len(after) == len(before)
        assert after[-1].node_name == "act"
        assert after[-1].sequence_number == before[-1].sequence_number + 1

    def test_pending_writes_round_trip(self, pg_engine, saver) -> None:
        execution_id = seed_execution(pg_engine, VPN)
        run(VPN, make_deps(), saver, execution_id, attempt=1)
        config = {"configurable": {"thread_id": str(execution_id)}}
        latest = saver.get_tuple(config)  # type: ignore[arg-type]
        assert latest is not None
        saver.put_writes(latest.config, [("output", {"x": 1})], task_id="task-1")
        saver.put_writes(latest.config, [("output", {"x": 2})], task_id="task-1")
        reread = saver.get_tuple(latest.config)
        assert reread is not None
        assert reread.pending_writes == [("task-1", "output", {"x": 1})]

    def test_list_and_lookup_by_id(self, pg_engine, saver) -> None:
        execution_id = seed_execution(pg_engine, VPN)
        run(VPN, make_deps(), saver, execution_id, attempt=1)
        config = {"configurable": {"thread_id": str(execution_id)}}
        history = list(saver.list(config))  # type: ignore[arg-type]
        assert len(history) == len(NODE_ORDER) + 2
        ids = [h.config["configurable"]["checkpoint_id"] for h in history]
        assert ids == sorted(ids, reverse=True)
        middle = history[5]
        assert saver.get_tuple(middle.config) is not None
        assert saver.get_tuple(middle.config).checkpoint["id"] == ids[5]  # type: ignore[union-attr]
        assert len(list(saver.list(config, limit=3))) == 3  # type: ignore[arg-type]
        assert len(list(saver.list(config, before=middle.config))) == len(history) - 6  # type: ignore[arg-type]
        assert saver.get_tuple({"configurable": {"thread_id": str(uuid4())}}) is None
