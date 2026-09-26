"""Server-owned permission classes for registered agent tools."""

from enum import StrEnum


class PermissionClass(StrEnum):
    """The enforcement class assigned to a tool by the server registry."""

    READ = "read"
    LOW_RISK_WRITE = "low_risk_write"
    HIGH_RISK = "high_risk"


__all__ = ["PermissionClass"]
