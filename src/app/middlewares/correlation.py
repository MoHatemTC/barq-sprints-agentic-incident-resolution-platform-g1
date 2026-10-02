import re
import time
import uuid
from collections.abc import Callable

import structlog
from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.core.correlation import clear_correlation_id, set_correlation_id
from app.middlewares.security_headers import register_security_headers

CORRELATION_ID_HEADER = "X-Correlation-ID"
#: What a caller-supplied id may look like. It is echoed in a response header and written to
#: logs and traces, so anything else (control characters, a megabyte of text) is replaced.
_VALID_CORRELATION_ID = re.compile(r"[A-Za-z0-9._:\-]{1,128}")
logger = structlog.getLogger("api.access")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Middleware that extracts or generates a correlation ID header,

    binds it to structlog contextvars, and injects it into the response.
    """

    def __init__(self, app: ASGIApp, header_name: str = CORRELATION_ID_HEADER) -> None:
        super().__init__(app)
        self.header_name = header_name

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        correlation_id = (request.headers.get(self.header_name) or "").strip()
        if not _VALID_CORRELATION_ID.fullmatch(correlation_id):
            correlation_id = str(uuid.uuid4())

        set_correlation_id(correlation_id)
        request.state.correlation_id = correlation_id

        start_time = time.perf_counter()
        try:
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.info(
                "http_request_completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
                client_ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
            )
            response.headers[self.header_name] = correlation_id
            return response
        except Exception:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.exception(
                "http_request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
                client_ip=request.client.host if request.client else None,
            )
            raise
        finally:
            clear_correlation_id()


def register_middlewares(app: FastAPI, header_name: str = CORRELATION_ID_HEADER) -> None:
    """Register all API middlewares on the FastAPI application."""
    app.add_middleware(CorrelationIdMiddleware, header_name=header_name)
    register_security_headers(app)
