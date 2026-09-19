"""Public API contract for middlewares (re-exports from app.middlewares)."""

from app.middlewares.correlation import (
    CORRELATION_ID_HEADER,
    CorrelationIdMiddleware,
    register_middlewares,
)

__all__ = [
    "CORRELATION_ID_HEADER",
    "CorrelationIdMiddleware",
    "register_middlewares",
]
