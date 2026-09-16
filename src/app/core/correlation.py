"""Correlation ID management and context tracking across async tasks."""
from __future__ import annotations

import uuid
from contextvars import ContextVar

import structlog

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def new_correlation_id() -> str:
    """Generate a standard 36-char UUIDv4 string."""
    return str(uuid.uuid4())


def set_correlation_id(cid: str) -> None:
    """Store the correlation ID in ContextVar and structlog contextvars."""
    _correlation_id.set(cid)
    structlog.contextvars.bind_contextvars(correlation_id=cid)


def clear_correlation_id() -> None:
    """Clear correlation ID from ContextVar and structlog contextvars."""
    _correlation_id.set(None)
    structlog.contextvars.clear_contextvars()


def get_correlation_id() -> str:
    """Return the current correlation ID, lazily generating one if absent."""
    try:
        ctx = structlog.contextvars.get_contextvars()
        cid = ctx.get("correlation_id")
        if cid:
            return str(cid)
    except Exception:
        pass

    cid = _correlation_id.get()
    if not cid:
        cid = new_correlation_id()
        _correlation_id.set(cid)
    return cid
