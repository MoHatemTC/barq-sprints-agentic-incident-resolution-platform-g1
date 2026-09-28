"""Evaluation and benchmarking router for platform diagnostic and model evaluation runs."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, status

from api.auth import verify_bearer_token
from api.schemas.eval import (
    EvalResultResponse,
    EvalRunRequest,
    EvalRunResponse,
)
from app.api.dependencies import get_app_settings
from app.core.config import Settings
from app.exceptions.app_errors import NotImplementedStubError

logger = structlog.getLogger("api.eval")

router = APIRouter(
    prefix="/api/v1/eval",
    tags=["Evaluation & Benchmarks"],
    dependencies=[Depends(verify_bearer_token)],
)

#: Returned when no evaluation engine has produced a result. Sprint 2 shipped this
#: router as a contract stub, and it fabricated a result on every read: a fixed
#: 0.92 / 0.94 / 342.5 ms with ``completed_at`` stamped from ``now()``, so two reads
#: seconds apart reported two different completion times for one fixed score. A
#: reviewer calling the endpoint twice saw the timestamp move and no work behind it.
#: Nothing is stored and no engine runs, so the honest answer is an empty list.
_NOT_BUILT = (
    "No evaluation engine is deployed. Sprint 2 shipped /api/v1/eval as a contract "
    "stub; the benchmark it described is not implemented, and this endpoint returns "
    "no results rather than synthetic ones."
)


@router.post(
    "/run",
    response_model=EvalRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger an evaluation run",
    description="Initiate an asynchronous benchmark evaluation run on a specified dataset.",
    responses={
        status.HTTP_501_NOT_IMPLEMENTED: {"description": "No evaluation engine is deployed."},
    },
)
async def trigger_eval_run(
    payload: EvalRunRequest,
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> EvalRunResponse:
    """Trigger a benchmark evaluation run.

    Refuses unless ``eval_benchmarks`` is enabled, and says so plainly. The flag ships
    ``False``; the previous implementation accepted the request, returned 202 with a
    generated ``run_id``, and then discarded it — the run never appeared in
    ``/results`` and produced no worker activity (dev407364, 2026-09-28).
    """
    if not settings.active_feature_flags.get("eval_benchmarks", False):
        logger.warning(
            "eval_run_refused",
            reason="eval_benchmarks_disabled",
            dataset_name=payload.dataset_name,
        )
        raise NotImplementedStubError(_NOT_BUILT)

    # Unreachable while the flag is off. Deliberately not fabricated: a run that is
    # accepted must be one this service can actually execute and report.
    raise NotImplementedStubError(_NOT_BUILT)  # pragma: no cover - guard, see _NOT_BUILT


@router.get(
    "/results",
    response_model=list[EvalResultResponse],
    status_code=status.HTTP_200_OK,
    summary="Get evaluation benchmark results",
    description="Retrieve scores, accuracy metrics, and latency statistics for evaluation runs.",
)
async def get_eval_results(
    run_id: str | None = Query(
        default=None,
        description="Optional filter by specific evaluation run ID",
    ),
) -> list[EvalResultResponse]:
    """Retrieve benchmark results.

    Returns an empty list. No evaluation engine is deployed, so there is nothing to
    report; see ``_NOT_BUILT``. An empty list is the truthful answer and is
    distinguishable from "a run scored zero" by its emptiness.
    """
    logger.info("eval_results_queried", run_id=run_id, returned=0)
    return []
