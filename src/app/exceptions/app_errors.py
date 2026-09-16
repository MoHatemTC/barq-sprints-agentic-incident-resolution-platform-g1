"""Global error taxonomy + standardized JSON error envelope."""

from __future__ import annotations

import logging
from typing import Any

from app.core.correlation import get_correlation_id
from app.utils.datetime import utc_now_iso

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base platform error
# ---------------------------------------------------------------------------
class AgenticPlatformError(Exception):
    """Base exception for all agentic platform errors."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def __str__(self) -> str:
        return f"[{self.status_code}] {self.message}" if self.status_code else self.message


# ---------------------------------------------------------------------------
# Taxonomy — declarative HTTP exceptions
# ---------------------------------------------------------------------------
class _PlatformHTTPError(AgenticPlatformError):
    """Internal base; do not raise directly."""

    code: str = "PLATFORM_ERROR"
    default_status_code: int = 500
    default_message: str = "An unexpected platform error occurred."

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("status_code", self.default_status_code)
        super().__init__(message if message is not None else self.default_message, **kwargs)

    def to_payload(self) -> dict[str, Any]:
        """Render this exception as the standardized envelope."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
                "correlation_id": get_correlation_id(),
                "timestamp": utc_now_iso(),
            }
        }


class AuthenticationError(_PlatformHTTPError):
    code = "AUTHENTICATION_FAILED"
    default_status_code = 401
    default_message = "Authentication required."


class PermissionDeniedError(_PlatformHTTPError):
    code = "PERMISSION_DENIED"
    default_status_code = 403
    default_message = "You do not have permission to perform this action."


class ResourceNotFoundError(_PlatformHTTPError):
    code = "RESOURCE_NOT_FOUND"
    default_status_code = 404
    default_message = "Resource not found."


class ContractValidationError(_PlatformHTTPError):
    code = "CONTRACT_VALIDATION_FAILED"
    default_status_code = 422
    default_message = "Request failed contract validation."

    def __init__(
        self,
        message: str | None = None,
        *,
        field_errors: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> None:
        details = dict(kwargs.pop("details", None) or {})
        if field_errors:
            details["field_errors"] = field_errors
        super().__init__(message, details=details, **kwargs)


class UnknownContractVersionError(ContractValidationError):
    code = "UNKNOWN_CONTRACT_VERSION"
    default_status_code = 422
    default_message = "Unknown contract version."


class ServiceUnavailableError(_PlatformHTTPError):
    code = "SERVICE_UNAVAILABLE"
    default_status_code = 503
    default_message = "Service temporarily unavailable."

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_seconds: int | None = None,
        **kwargs: Any,
    ) -> None:
        self.retry_after_seconds = retry_after_seconds
        details = dict(kwargs.pop("details", None) or {})
        if retry_after_seconds is not None:
            details["retry_after_seconds"] = retry_after_seconds
        super().__init__(message, details=details, **kwargs)


class NotImplementedStubError(_PlatformHTTPError):
    code = "NOT_IMPLEMENTED"
    default_status_code = 501
    default_message = "This capability is not implemented yet."


def error_envelope(
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "correlation_id": correlation_id or get_correlation_id(),
            "timestamp": utc_now_iso(),
        }
    }
