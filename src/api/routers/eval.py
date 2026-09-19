"""Evaluation and benchmarking router for platform diagnostic and model evaluation runs."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, Query, status

from api.auth import verify_bearer_token
from api.schemas.eval import (
    EvalResultResponse,
    EvalRunRequest,
    EvalRunResponse,
)

logger = structlog.getLogger("api.eval")

router = APIRouter(
    prefix="/api/v1/eval",
    tags=["Evaluation & Benchmarks"],
    dependencies=[Depends(verify_bearer_token)],
)


@router.post(
    "/run",
    response_model=EvalRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger an evaluation run",
    description="Initiate an asynchronous benchmark evaluation run on a specified dataset.",
)
async def trigger_eval_run(
    payload: EvalRunRequest,
) -> EvalRunResponse:
    """Trigger a benchmark evaluation run.

    Under Sprint 2 contract stub semantics, returns a schema-compliant accepted response
    with a uniquely generated run_id.
    """
    run_id = f"eval-{uuid4().hex[:12]}"
    now_iso = datetime.now(UTC).isoformat()
    logger.info(
        "eval_run_triggered",
        run_id=run_id,
        dataset_name=payload.dataset_name,
        sample_size=payload.sample_size,
    )

    return EvalRunResponse(
        run_id=run_id,
        status="started",
        created_at=now_iso,
    )


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

    Under Sprint 2 contract stub semantics, returns a schema-compliant result record.
    """
    logger.info("eval_results_queried", run_id=run_id)

    target_run_id = run_id or "eval-baseline-sample-001"
    now_iso = datetime.now(UTC).isoformat()

    return [
        EvalResultResponse(
            run_id=target_run_id,
            dataset_name="servicenow-incident-benchmarks-v1",
            benchmark_score=0.92,
            accuracy=0.94,
            p95_latency_ms=342.5,
            completed_at=now_iso,
        )
    ]
