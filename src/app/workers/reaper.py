"""Crash reaper: reclaim executions orphaned by a dead worker.

A worker killed mid-graph (SIGKILL, OOM, host loss) leaves its ``executions`` row at
``running`` forever. Celery's ``task_reject_on_worker_lost`` redelivers the
*message*, so a redelivery would normally re-claim the row — but only if the
delivery actually happens. When it does not (the worker died holding a message the
broker had already acked as lost, or the reaper has to cope with a row whose
delivery is long gone) nothing else in the system ever revisits the row, and it
stays ``running`` indefinitely.

Measured on dev407364, 2026-09-28: SIGKILL of the Celery worker while an execution
sat at the ``validate`` node left it ``running`` at that node 90 seconds after the
worker was restarted, and it never moved again. 96 such rows had already
accumulated. ``abandoned`` was in ``ck_executions_status`` the whole time; nothing
ever set it.

The reaper closes that gap. It is deliberately conservative:

* the window is ``worker_time_limit`` plus a grace margin, so a run is only
  reclaimed once Celery's own hard limit has already passed — anything still
  ``running`` past that is definitionally not making progress, not merely slow;
* a row with a NULL ``heartbeat_at`` predates the lease columns and is treated as
  stale, since nothing is known to be holding it;
* the heartbeat is only stamped on claim, so a legitimately long run keeps its
  original stamp and the window measures from the start of the attempt rather than
  from the last node — which is the honest reading of "this attempt is overdue".
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from uuid import UUID

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.models import Event, Execution, Failure, RetryState

logger = structlog.getLogger("workers.reaper")

#: Extra head-room on top of ``worker_time_limit`` before a run is called dead.
#: Covers the gap between Celery noticing the worker is gone and the broker
#: settling, plus clock skew between the API host and the database.
DEFAULT_GRACE_SECONDS = 120

#: Statuses a reaper is allowed to reclaim. Deliberately excludes
#: ``awaiting_approval``: a paused run is waiting for a human, which can take days,
#: and it is not orphaned.
RECLAIMABLE_STATUSES = ("running",)

#: ``failures.failure_type`` recorded when a run's worker died; one per run at most.
WORKER_LOST = "worker_lost"


@dataclass(frozen=True)
class ReaperReport:
    """What one sweep did, for the beat log and for tests."""

    reclaimed: tuple[UUID, ...] = ()
    examined: int = 0
    #: Runs put back in the queue for one more attempt (first crash only).
    requeued: tuple[UUID, ...] = ()


def reap_stale_executions(
    session: Session,
    *,
    time_limit_seconds: int,
    grace_seconds: int = DEFAULT_GRACE_SECONDS,
    now: dt.datetime | None = None,
    requeue_once: bool = False,
) -> ReaperReport:
    """Mark overdue ``running`` executions ``abandoned`` — or, with ``requeue_once``,
    put a run whose worker died for the first time back to ``queued`` so the caller
    re-sends it (``WORKER_LOST`` failure row marks the crash; a second crash abandons).

    Seen live on 2026-10-03: the memory killer stopped a worker mid-ticket, the
    redelivery never came, and the ticket stayed New with no team and no message.

    ``abandoned`` is terminal, and ``ck_executions_terminal_state`` requires
    ``ended_at`` and a non-empty ``termination_cause`` alongside it, so both are
    written here in the same statement. The incident's own ``ai_processing_state``
    is deliberately left alone: reaping a run says nothing about what a human should
    do to the incident, and inventing a failure reason for it would be a claim the
    system cannot support.
    """
    moment = now or dt.datetime.now(dt.UTC)
    cutoff = moment - dt.timedelta(seconds=time_limit_seconds + grace_seconds)

    stale = list(
        session.execute(
            select(Execution.execution_id, Execution.worker_id).where(
                Execution.status.in_(RECLAIMABLE_STATUSES),
                # NULL heartbeats predate the lease columns; nothing holds them.
                (Execution.heartbeat_at.is_(None)) | (Execution.heartbeat_at < cutoff),
            )
        )
    )
    if not stale:
        return ReaperReport()

    found = [row.execution_id for row in stale]
    holders = {row.execution_id: row.worker_id for row in stale}

    requeue: list[UUID] = []
    if requeue_once:
        crashed_before = set(
            session.scalars(
                select(Failure.execution_id).where(
                    Failure.execution_id.in_(found), Failure.failure_type == WORKER_LOST
                )
            )
        )
        requeue = [execution_id for execution_id in found if execution_id not in crashed_before]
    for execution_id in requeue:
        session.add(
            Failure(
                execution_id=execution_id,
                attempt=1,
                failure_type=WORKER_LOST,
                message="the worker running it stopped (killed or restarted); queued again once",
                retryable=True,
            )
        )
    if requeue:
        session.execute(
            update(Execution)
            .where(Execution.execution_id.in_(requeue), Execution.status == "running")
            .values(status="queued", heartbeat_at=None)
        )
        for execution_id in requeue:
            logger.warning("execution_requeued_after_crash", execution_id=str(execution_id))
    ids = [execution_id for execution_id in found if execution_id not in requeue]
    if not ids:
        return ReaperReport(examined=len(found), requeued=tuple(requeue))

    session.execute(
        update(Execution)
        .where(Execution.execution_id.in_(ids))
        .values(
            status="abandoned",
            # The *database* clock, not the host's. ck_executions_time_order requires
            # ended_at >= started_at, and started_at was stamped by the database when
            # the run was accepted; a host whose clock trails the server's would
            # otherwise write a terminal timestamp that precedes the start and abort
            # the whole sweep on the first orphan.
            ended_at=func.now(),
            termination_cause=(
                "crash_reaped: execution still 'running' after the worker time limit "
                "(worker_time_limit + grace) elapsed, so the worker holding it is gone"
            ),
            heartbeat_at=None,
        )
    )
    # A reclaimed run must not sit in retry_state as retryable forever.
    #
    # 'cancelled', not 'abandoned': ck_retry_state_state only admits
    # ('ready', 'scheduled', 'exhausted', 'succeeded', 'cancelled'), so writing
    # 'abandoned' here would violate the check constraint and abort the whole sweep
    # on its first orphan — a reaper that dies on the first row it tries to fix.
    # 'cancelled' is also the honest semantic: the retry loop is not going to run
    # again, and the run did not succeed.
    session.execute(
        update(RetryState)
        .where(RetryState.execution_id.in_(ids))
        .values(state="cancelled", next_retry_at=None)
    )

    for execution_id in ids:
        logger.warning(
            "execution_abandoned",
            execution_id=str(execution_id),
            last_worker=holders.get(execution_id),
            cutoff=cutoff.isoformat(),
        )
    return ReaperReport(reclaimed=tuple(ids), examined=len(found), requeued=tuple(requeue))


def event_payloads(session: Session, execution_ids: tuple[UUID, ...]) -> dict[UUID, dict]:
    """The original contract-v1 event of each execution, to send it to a worker again."""
    if not execution_ids:
        return {}
    rows = session.execute(
        select(
            Execution.execution_id,
            Event.event_id,
            Event.incident_sys_id,
            Event.incident_number,
            Event.event_type,
            Event.contract_version,
        )
        .join(Event, Event.id == Execution.event_record_id)
        .where(Execution.execution_id.in_(execution_ids))
    )
    return {
        row.execution_id: {
            "event_id": row.event_id,
            "sys_id": row.incident_sys_id,
            "number": row.incident_number,
            "event_type": row.event_type,
            "contract_version": row.contract_version,
        }
        for row in rows
    }


__all__ = [
    "WORKER_LOST",
    "event_payloads",
    "DEFAULT_GRACE_SECONDS",
    "RECLAIMABLE_STATUSES",
    "ReaperReport",
    "reap_stale_executions",
]
