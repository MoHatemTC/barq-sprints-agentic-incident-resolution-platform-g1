"""Interrupt payloads and ServiceNow write receipts (S3.4, NFR-03 / NFR-07).

LangGraph already checkpoints the interrupt value. These rows are the *audit*
copy that GET /approvals and crash-recovery can read without reconstituting a
graph: ``hitl.interrupt`` is the paused lifecycle, ``servicenow.write`` is the
write-boundary receipt. An execution that has a write receipt but never paused
is a crash-recovery story; one that has ``hitl.interrupt`` then a later write
receipt is an interrupt-resume story.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import Execution, ExecutionNodeState
from app.workers.sync_engine import SyncSessionFactory

HITL_NODE = "hitl.interrupt"
WRITE_NODE = "servicenow.write"


class GraphAuditStore(Protocol):
    def save_interrupt(self, execution_id: str, payload: dict[str, Any]) -> None: ...

    def get_interrupt(self, execution_id: str) -> dict[str, Any] | None: ...

    def save_receipt(self, execution_id: str, receipt: dict[str, Any]) -> None: ...

    def get_receipt(self, execution_id: str) -> dict[str, Any] | None: ...


class MemoryGraphAuditStore:
    """Process-local store for unit tests (no Postgres)."""

    def __init__(self) -> None:
        self.interrupts: dict[str, dict[str, Any]] = {}
        self.receipts: dict[str, dict[str, Any]] = {}

    def save_interrupt(self, execution_id: str, payload: dict[str, Any]) -> None:
        self.interrupts[execution_id] = payload

    def get_interrupt(self, execution_id: str) -> dict[str, Any] | None:
        return self.interrupts.get(execution_id)

    def save_receipt(self, execution_id: str, receipt: dict[str, Any]) -> None:
        self.receipts[execution_id] = receipt

    def get_receipt(self, execution_id: str) -> dict[str, Any] | None:
        return self.receipts.get(execution_id)


class PostgresGraphAuditStore:
    """Durable copy in ``workflow_state``, sharing S2.2's unique key."""

    def __init__(self, session_factory: SyncSessionFactory) -> None:
        self._session_factory = session_factory

    def save_interrupt(self, execution_id: str, payload: dict[str, Any]) -> None:
        self._upsert(execution_id, HITL_NODE, "awaiting_approval", payload)

    def get_interrupt(self, execution_id: str) -> dict[str, Any] | None:
        return self._get(execution_id, HITL_NODE)

    def save_receipt(self, execution_id: str, receipt: dict[str, Any]) -> None:
        self._upsert(execution_id, WRITE_NODE, "succeeded", receipt)

    def get_receipt(self, execution_id: str) -> dict[str, Any] | None:
        return self._get(execution_id, WRITE_NODE)

    def _get(self, execution_id: str, node: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(ExecutionNodeState)
                .where(
                    ExecutionNodeState.execution_id == UUID(execution_id),
                    ExecutionNodeState.node_name == node,
                )
                .order_by(ExecutionNodeState.sequence_number.desc())
            )
            if row is None or not isinstance(row.decision, dict):
                return None
            return dict(row.decision)

    def _upsert(self, execution_id: str, node: str, status: str, decision: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        eid = UUID(execution_id)
        with self._session_factory() as session, session.begin():
            # Sequence numbers come from ``max(sequence_number)``, and LangGraph
            # overlaps a running node with the previous nodes' checkpoint writes.
            # ``WorkflowStateSaver.put`` locks the execution row before it reads
            # that maximum; taking the same lock first is what keeps the two
            # writers off the same sequence number (they otherwise deadlock, or
            # one loses on ``uq_workflow_state_execution_sequence``).
            session.execute(
                select(Execution.execution_id)
                .where(Execution.execution_id == eid)
                .with_for_update()
            )
            sequence = _next_sequence(session, eid)
            stmt = insert(ExecutionNodeState).values(
                execution_id=eid,
                sequence_number=sequence,
                node_name=node,
                attempt=1,
                status=status,
                started_at=now,
                ended_at=now,
                evidence=[],
                decision=decision,
                state_snapshot={"kind": node, "lifecycle": decision.get("lifecycle")},
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_workflow_state_execution_node_attempt",
                set_={
                    "sequence_number": stmt.excluded.sequence_number,
                    "status": stmt.excluded.status,
                    "ended_at": stmt.excluded.ended_at,
                    "decision": stmt.excluded.decision,
                    "state_snapshot": stmt.excluded.state_snapshot,
                },
            )
            session.execute(stmt)


def _next_sequence(session: Session, execution_id: UUID) -> int:
    from sqlalchemy import func

    current = session.scalar(
        select(func.coalesce(func.max(ExecutionNodeState.sequence_number), 0)).where(
            ExecutionNodeState.execution_id == execution_id
        )
    )
    return int(current or 0) + 1


def build_audit_store(backend: str | None = None, settings: Any = None) -> GraphAuditStore:
    """Build the audit store for ``backend`` — the same name the checkpointer takes.

    ``settings`` are the *app* :class:`~app.core.config.Settings` (they carry the
    ``postgres_*`` fields the sync URL needs); ``AgentSettings`` deliberately does
    not. Both are resolved here so no caller has to know which settings object the
    store wants.
    """
    if backend is None:
        from agent.config import get_agent_settings

        backend = get_agent_settings().agent_checkpointer_backend
    if backend == "memory":
        return MemoryGraphAuditStore()
    from app.core.config import get_settings
    from app.workers.sync_engine import (
        build_sync_database_url,
        create_sync_engine,
        create_sync_session_factory,
    )

    engine = create_sync_engine(build_sync_database_url(settings or get_settings()))
    return PostgresGraphAuditStore(create_sync_session_factory(engine))


__all__ = [
    "HITL_NODE",
    "WRITE_NODE",
    "GraphAuditStore",
    "MemoryGraphAuditStore",
    "PostgresGraphAuditStore",
    "build_audit_store",
]
