"""Public API contract for global error taxonomy and exception handling.

Re-exports from app.exceptions.app_errors and app.exceptions.handlers.
"""

from __future__ import annotations

from app.core.constants import ERROR_STATUS_MAP
from app.exceptions.app_errors import (
    AgenticPlatformError,
    AuthenticationError,
    ContractValidationError,
    NotImplementedStubError,
    PermissionDeniedError,
    ResourceNotFoundError,
    ServiceUnavailableError,
    UnknownContractVersionError,
    error_envelope,
)
from app.exceptions.handlers import (
    http_exception_handler,
    platform_error_handler,
    register_exception_handlers,
    validation_error_handler,
)

__all__ = [
    "ERROR_STATUS_MAP",
    "AgenticPlatformError",
    "AuthenticationError",
    "ContractValidationError",
    "NotImplementedStubError",
    "PermissionDeniedError",
    "ResourceNotFoundError",
    "ServiceUnavailableError",
    "UnknownContractVersionError",
    "error_envelope",
    "http_exception_handler",
    "platform_error_handler",
    "register_exception_handlers",
    "validation_error_handler",
]
