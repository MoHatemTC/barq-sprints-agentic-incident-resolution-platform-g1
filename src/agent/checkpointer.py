"""LangGraph checkpointer backed by S2.2's ``workflow_state`` table (FR-12, NFR-03).

One row per checkpoint, i.e. one row per node the graph completed:

===================  ==========================================================
``execution_id``     the LangGraph ``thread_id`` — one thread per execution
``sequence_number``  1, 2, 3… in the order the checkpoints were written
``node_name``        the node that produced the checkpoint (``__input__`` and
                     ``__start__`` for the two bootstrap checkpoints)
``attempt``          the Celery delivery attempt that ran the node
``status``           ``succeeded`` / ``blocked`` / ``skipped`` /
                     ``awaiting_approval`` (act) or ``started`` (bootstrap)
``decision``         the routing-relevant verdict the node recorded
``evidence``         citations, for ``retrieve`` and ``generate``
``state_snapshot``   the serialized LangGraph checkpoint, its metadata, the
                     channel blobs written at this step and pending writes
===================  ==========================================================

Storage mirrors LangGraph's reference ``InMemorySaver``: a checkpoint stores only
the channel values that changed at its step (``new_versions``), and a read rebuilds
the full state from the blobs of every row of the thread. Blobs are LangGraph's own
typed serialization, base64-encoded so they fit a JSONB object.

A retried delivery (attempt n+1) resumes from the latest row: nodes that already
completed are not re-run, so no ServiceNow write is repeated by a retry.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from sqlalchemy import func, null, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import Execution, ExecutionNodeState
from app.workers.sync_engine import SyncSessionFactory

INPUT_NODE = "__input__"
START_NODE = "__start__"

#: Node → the state section that holds its routing decision.
DECISION_SECTIONS: dict[str, str] = {
    "validate": "eligibility",
    "classify": "classification",
    "determine_risk": "risk",
    "diagnose": "diagnosis",
    "verify_evidence": "verification",
    "safety_check": "safety",
    "confidence_check": "confidence",
}


def _b64(typed: tuple[str, bytes]) -> dict[str, str]:
    return {"type": typed[0], "data": base64.b64encode(typed[1]).decode("ascii")}


def _unb64(value: dict[str, str]) -> tuple[str, bytes]:
    return value["type"], base64.b64decode(value["data"])


def node_name_for(values: dict[str, Any], metadata: CheckpointMetadata) -> str:
    if metadata.get("source") == "input":
        return INPUT_NODE
    node = values.get("current_node")
    if not node or node == INPUT_NODE:
        return START_NODE
    return str(node)


def _slim_retrieval(section: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in section.items() if k != "hits"} | {
        "hit_count": len(section.get("hits", []))
    }


def summarize(
    node: str, values: dict[str, Any]
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    """(status, decision, evidence) for a checkpoint row."""
    if node in (INPUT_NODE, START_NODE):
        return "started", None, []
    if node == "load":
        incident = values.get("incident") or {}
        incident_keys = ("number", "priority", "impact", "urgency", "category", "service", "state")
        return "succeeded", {k: incident.get(k) for k in incident_keys}, []
    if node == "retrieve":
        retrieval = values.get("retrieval") or {}
        evidence = [
            {
                "article_id": h.get("article_id"),
                "section": h.get("section"),
                "relevance": h.get("relevance"),
                "fused_score": h.get("fused_score"),
            }
            for h in retrieval.get("hits", [])
        ]
        return "succeeded", _slim_retrieval(retrieval), evidence
    if node == "generate":
        draft = values.get("draft") or {}
        evidence = [{"source": s} for s in draft.get("sources", [])]
        draft_summary = {
            "steps": len(draft.get("steps", [])),
            "dropped_steps": draft.get("dropped_steps"),
        }
        return "succeeded", draft_summary, evidence
    if node == "act":
        output = values.get("output") or {}
        outcome = str(output.get("outcome", ""))
        if outcome.startswith("skipped"):
            status = "skipped"
        elif outcome.startswith("escalated"):
            status = "blocked"
        else:
            status = "awaiting_approval"
        output_keys = ("outcome", "processing_state", "confidence", "classification", "write_back")
        return status, {k: output.get(k) for k in output_keys}, []
    section = DECISION_SECTIONS.get(node)
    recorded = values.get(section) if section else None
    return "succeeded", recorded if isinstance(recorded, dict) else None, []


class WorkflowStateSaver(BaseCheckpointSaver[str]):
    """Sync checkpointer over ``workflow_state`` (the Celery worker is sync)."""

    def __init__(self, session_factory: SyncSessionFactory, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._session_factory = session_factory

    @contextmanager
    def _session(self) -> Iterator[Session]:
        with self._session_factory() as session, session.begin():
            yield session

    # -- reads -------------------------------------------------------------------------

    def _rows(self, session: Session, execution_id: UUID) -> list[ExecutionNodeState]:
        stmt = (
            select(ExecutionNodeState)
            .where(ExecutionNodeState.execution_id == execution_id)
            .order_by(ExecutionNodeState.sequence_number)
        )
        return [row for row in session.scalars(stmt) if row.state_snapshot]

    def _tuple(
        self, thread_id: str, row: ExecutionNodeState, rows: list[ExecutionNodeState]
    ) -> CheckpointTuple:
        snap = row.state_snapshot or {}
        checkpoint: Checkpoint = self.serde.loads_typed(_unb64(snap["checkpoint"]))
        blobs: dict[tuple[str, str], dict[str, str]] = {}
        for other in rows:
            for channel, blob in (other.state_snapshot or {}).get("blobs", {}).items():
                blobs[(channel, str(blob["version"]))] = blob
        values: dict[str, Any] = {}
        for channel, version in checkpoint["channel_versions"].items():
            blob = blobs.get((channel, str(version)))
            if blob is not None and blob["type"] != "empty":
                values[channel] = self.serde.loads_typed(_unb64(blob))
        ns = snap.get("checkpoint_ns", "")
        parent_id = snap.get("parent_checkpoint_id")
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": ns,
                    "checkpoint_id": snap["checkpoint_id"],
                }
            },
            checkpoint={**checkpoint, "channel_values": values},
            metadata=self.serde.loads_typed(_unb64(snap["metadata"])),
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": ns,
                        "checkpoint_id": parent_id,
                    }
                }
                if parent_id
                else None
            ),
            pending_writes=[
                (w["task_id"], w["channel"], self.serde.loads_typed(_unb64(w["value"])))
                for w in snap.get("writes", [])
            ],
        )

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = str(config["configurable"]["thread_id"])
        wanted = get_checkpoint_id(config)
        with self._session() as session:
            rows = self._rows(session, UUID(thread_id))
            if not rows:
                return None
            if wanted is None:
                return self._tuple(thread_id, rows[-1], rows)
            for row in rows:
                if (row.state_snapshot or {}).get("checkpoint_id") == wanted:
                    return self._tuple(thread_id, row, rows)
        return None

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        if config is None:
            return
        thread_id = str(config["configurable"]["thread_id"])
        before_id = get_checkpoint_id(before) if before else None
        with self._session() as session:
            rows = self._rows(session, UUID(thread_id))
            tuples = [self._tuple(thread_id, row, rows) for row in reversed(rows)]
        count = 0
        for item in tuples:
            if before_id and item.config["configurable"]["checkpoint_id"] >= before_id:
                continue
            if filter and any(item.metadata.get(k) != v for k, v in filter.items()):
                continue
            yield item
            count += 1
            if limit is not None and count >= limit:
                return

    # -- writes ------------------------------------------------------------------------

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        execution_id = UUID(thread_id)
        ns = configurable.get("checkpoint_ns", "")
        attempt = int(configurable.get("attempt", 1))

        stored = checkpoint.copy()
        values: dict[str, Any] = stored.pop("channel_values")  # type: ignore[misc]
        blobs = {
            channel: {"version": str(version), "type": "empty", "data": ""}
            if channel not in values
            else {"version": str(version), **_b64(self.serde.dumps_typed(values[channel]))}
            for channel, version in new_versions.items()
        }
        node = node_name_for(values, metadata)
        status, decision, evidence = summarize(node, values)
        now = datetime.now(UTC)
        snapshot = {
            "checkpoint_id": checkpoint["id"],
            "parent_checkpoint_id": configurable.get("checkpoint_id"),
            "checkpoint_ns": ns,
            "step": metadata.get("step"),
            "source": metadata.get("source"),
            "checkpoint": _b64(self.serde.dumps_typed(stored)),
            "metadata": _b64(self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))),
            "blobs": blobs,
            "writes": [],
        }
        with self._session() as session:
            # Serialise sequence allocation per execution.
            session.execute(
                select(Execution.execution_id)
                .where(Execution.execution_id == execution_id)
                .with_for_update()
            )
            sequence = (
                session.scalar(
                    select(func.coalesce(func.max(ExecutionNodeState.sequence_number), 0)).where(
                        ExecutionNodeState.execution_id == execution_id
                    )
                )
                or 0
            ) + 1
            row = {
                "execution_id": execution_id,
                "sequence_number": sequence,
                "node_name": node,
                "attempt": attempt,
                "status": status,
                "started_at": now,
                "ended_at": now,
                "evidence": evidence,
                # SQL NULL, not JSON null: ck_workflow_state_decision_object.
                "decision": decision if decision is not None else null(),
                "state_snapshot": snapshot,
            }
            stmt = insert(ExecutionNodeState).values(**row)
            # A redelivery that re-runs a node under the same attempt number (hard
            # kill before the checkpoint committed) replaces that node's row.
            stmt = stmt.on_conflict_do_update(
                constraint="uq_workflow_state_execution_node_attempt",
                set_={
                    "sequence_number": stmt.excluded.sequence_number,
                    "status": stmt.excluded.status,
                    "ended_at": stmt.excluded.ended_at,
                    "evidence": stmt.excluded.evidence,
                    "decision": stmt.excluded.decision,
                    "state_snapshot": stmt.excluded.state_snapshot,
                },
            )
            session.execute(stmt)
            if node not in (INPUT_NODE, START_NODE):
                session.execute(
                    update(Execution)
                    .where(Execution.execution_id == execution_id)
                    .values(node_reached=node)
                )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        configurable = config["configurable"]
        execution_id = UUID(str(configurable["thread_id"]))
        checkpoint_id = configurable["checkpoint_id"]
        with self._session() as session:
            rows = session.scalars(
                select(ExecutionNodeState)
                .where(ExecutionNodeState.execution_id == execution_id)
                .with_for_update()
            ).all()
            target = next(
                (r for r in rows if (r.state_snapshot or {}).get("checkpoint_id") == checkpoint_id),
                None,
            )
            if target is None:
                return
            snapshot = dict(target.state_snapshot or {})
            existing = list(snapshot.get("writes", []))
            seen = {(w["task_id"], w["idx"]) for w in existing}
            for idx, (channel, value) in enumerate(writes):
                key = (task_id, WRITES_IDX_MAP.get(channel, idx))
                if key[1] >= 0 and key in seen:
                    continue
                existing = [w for w in existing if (w["task_id"], w["idx"]) != key]
                existing.append(
                    {
                        "task_id": task_id,
                        "idx": key[1],
                        "channel": channel,
                        "value": _b64(self.serde.dumps_typed(value)),
                        "task_path": task_path,
                    }
                )
            snapshot["writes"] = existing
            target.state_snapshot = snapshot

    def delete_thread(self, thread_id: str) -> None:
        with self._session() as session:
            for row in self._rows(session, UUID(str(thread_id))):
                session.delete(row)

    # The worker is sync; async callers get the same behaviour.
    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self.get_tuple(config)

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self.put_writes(config, writes, task_id, task_path)


def build_checkpointer(backend: str, settings: Any = None) -> BaseCheckpointSaver[Any]:
    """'postgres' → :class:`WorkflowStateSaver`; 'memory' → LangGraph's InMemorySaver."""
    if backend == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()
    from app.core.config import get_settings
    from app.workers.sync_engine import (
        build_sync_database_url,
        create_sync_engine,
        create_sync_session_factory,
    )

    engine = create_sync_engine(build_sync_database_url(settings or get_settings()))
    return WorkflowStateSaver(create_sync_session_factory(engine))


__all__ = [
    "INPUT_NODE",
    "START_NODE",
    "WorkflowStateSaver",
    "build_checkpointer",
    "node_name_for",
    "summarize",
]
