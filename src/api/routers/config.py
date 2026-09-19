"""Runtime configuration router providing sanitized settings inspection."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, status

from api.auth import verify_bearer_token
from api.schemas.config import RedactedConfigResponse
from app.api.dependencies import get_app_settings
from app.core.config import Settings

logger = structlog.getLogger("api.config")

router = APIRouter(
    prefix="/api/v1",
    tags=["Configuration"],
    dependencies=[Depends(verify_bearer_token)],
)


@router.get(
    "/config",
    response_model=RedactedConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Get sanitized runtime configuration",
    description="Retrieve application runtime configuration with all sensitive secrets redacted.",
)
async def get_config(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> RedactedConfigResponse:
    """Return runtime configuration metadata with all sensitive secrets redacted."""
    logger.info("runtime_config_inspected")
    return RedactedConfigResponse.from_settings(settings)
