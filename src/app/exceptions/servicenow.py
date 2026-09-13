from __future__ import annotations


class ServiceNowError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def __str__(self) -> str:
        return f"[{self.status_code}] {self.message}" if self.status_code else self.message


class ServiceNowAuthenticationError(ServiceNowError):
    """Bad credentials at the token endpoint, OR still 401 after one refresh+retry."""


class ServiceNowAuthorizationError(ServiceNowError):
    """403 - ServiceNow rejected the request due to insufficient permissions."""


class ServiceNowNotFoundError(ServiceNowError):
    """404 - sys_id does not exist."""


class ServiceNowConflictError(ServiceNowError):
    """409 - Conflict (e.g. idempotency or duplicate)."""


class ServiceNowValidationError(ServiceNowError):
    """400/422 - ServiceNow rejected the payload itself. Retrying the same
    payload will never succeed."""


class ServiceNowRateLimitError(ServiceNowError):
    """429. Carries retry_after (seconds) when ServiceNow supplies it."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ServiceNowServerError(ServiceNowError):
    """5xx. Transient - safe to retry with backoff."""


class ServiceNowTimeoutError(ServiceNowError):
    """Request exceeded servicenow_timeout_seconds. Safe to retry."""


class ServiceNowConnectionError(ServiceNowError):
    """DNS/TCP failure before any response arrived. Safe to retry."""


class ServiceNowHumanLockError(ServiceNowError):
    """Raised when an incident is locked for human review and cannot be modified by the AI agent."""


class ServiceNowWriteRejectedError(ServiceNowError):
    """ServiceNow returned 2xx but did not persist one or more requested fields."""
