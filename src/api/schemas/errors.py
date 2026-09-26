"""Schema for the platform's unified error envelope.

Every non-2xx response the API produces is built by
:func:`app.exceptions.app_errors.error_envelope`, so this is the one shape a
client has to handle. It is declared here so the OpenAPI document can describe
the error responses: the routes return 401, 403, 404, 409 and 503 in practice,
and a client generated from a spec that lists only 200/202/422 has no way to
know that.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    """The ``error`` object inside the envelope."""

    code: str = Field(
        ...,
        description="Stable machine-readable code, e.g. AUTHENTICATION_FAILED, "
        "PERMISSION_DENIED, RESOURCE_CONFLICT, SERVICE_UNAVAILABLE.",
    )
    message: str = Field(..., description="Human-readable explanation.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured context. For a 422 this carries field_errors.",
    )
    correlation_id: str | None = Field(
        default=None,
        description="Correlates the response with the server log and the trace.",
    )
    timestamp: datetime = Field(..., description="When the error was produced.")


class ErrorResponse(BaseModel):
    """The unified error envelope returned by every failing endpoint."""

    error: ErrorBody
