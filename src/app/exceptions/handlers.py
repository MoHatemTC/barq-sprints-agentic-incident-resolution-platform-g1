"""FastAPI exception handlers translating platform errors to standardized JSON envelopes."""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.exceptions.app_errors import (
    AgenticPlatformError,
    _PlatformHTTPError,
    error_envelope,
)

logger = structlog.getLogger("api.errors")


async def platform_error_handler(request: Request, exc: AgenticPlatformError) -> JSONResponse:
    """Handle all AgenticPlatformError exceptions and return standardized error envelopes."""
    if isinstance(exc, _PlatformHTTPError):
        status_code = exc.status_code or exc.default_status_code
        payload = exc.to_payload()
    else:
        status_code = exc.status_code or 500
        payload = error_envelope(
            code="PLATFORM_ERROR",
            message=exc.message,
            details=exc.details,
        )

    logger.warning(
        "platform_error_handled",
        status_code=status_code,
        error_code=payload["error"]["code"],
        message=payload["error"]["message"],
        path=request.url.path,
    )
    return JSONResponse(status_code=status_code, content=payload)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # If the validation error was caused by an UnknownContractVersionError,
    # preserve its specific error code
    for err in exc.errors():
        ctx_err = err.get("ctx", {}).get("error")
        if isinstance(ctx_err, _PlatformHTTPError):
            return await platform_error_handler(request, ctx_err)

    field_errors = [
        {
            "field": ".".join(str(loc) for loc in err.get("loc", [])),
            "message": err.get("msg", ""),
            "type": err.get("type", ""),
        }
        for err in exc.errors()
    ]
    payload = error_envelope(
        code="CONTRACT_VALIDATION_FAILED",
        message="Request failed contract validation.",
        details={"field_errors": field_errors},
    )
    logger.warning(
        "validation_error_handled",
        status_code=422,
        error_code="CONTRACT_VALIDATION_FAILED",
        path=request.url.path,
    )
    return JSONResponse(status_code=422, content=payload)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handle standard HTTPExceptions (e.g. 404, 503 from dependencies)."""
    payload = error_envelope(
        code=f"HTTP_{exc.status_code}",
        message=str(exc.detail),
    )
    return JSONResponse(status_code=exc.status_code, content=payload)


def register_exception_handlers(app: FastAPI) -> None:
    """Register unified exception handlers on the FastAPI application."""
    # Starlette's expected handler signatures are narrower than the ones it
    # actually supports at runtime; these registrations are correct.
    app.add_exception_handler(AgenticPlatformError, platform_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
