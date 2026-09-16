"""Application-wide constants and status mappings."""
from __future__ import annotations

from app.exceptions.app_errors import (
    AgenticPlatformError,
    AuthenticationError,
    ContractValidationError,
    NotImplementedStubError,
    PermissionDeniedError,
    ResourceNotFoundError,
    ServiceUnavailableError,
    UnknownContractVersionError,
)

ERROR_STATUS_MAP: dict[type[AgenticPlatformError], int] = {
    AuthenticationError: 401,
    PermissionDeniedError: 403,
    ResourceNotFoundError: 404,
    ContractValidationError: 422,
    UnknownContractVersionError: 422,
    ServiceUnavailableError: 503,
    NotImplementedStubError: 501,
}

__all__ = [
    "ERROR_STATUS_MAP",
]
