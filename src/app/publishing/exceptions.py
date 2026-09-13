"""Domain exceptions for ServiceNow Knowledge Base publishing."""

from __future__ import annotations


class ServiceNowKBError(Exception):
    """Base error for kb_knowledge publishing failures."""


class ServiceNowAuthError(ServiceNowKBError):
    """Credentials rejected (HTTP 401)."""


class ServiceNowAccessError(ServiceNowKBError):
    """Account lacks permission for the operation (HTTP 403)."""


class ServiceNowKBSchemaError(ServiceNowKBError):
    """The kb_knowledge table is missing the expected custom setup (HTTP 400)."""


class ServiceNowRequestError(ServiceNowKBError):
    """Unexpected Table API response or HTTP transport failure."""


class ServiceNowWriteRejectedError(ServiceNowKBError):
    """Read-back verification after a write did not match what was sent."""
