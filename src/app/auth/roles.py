"""Role definitions and hierarchical Role-Based Access Control (RBAC) enumerations."""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    """Platform User Roles for Role-Based Access Control (RBAC).

    Hierarchy:
    - ADMIN (level 3): Unrestricted administrative and system privileges.
    - OPERATOR (level 2): Incident remediation, approval decision-making, and DLQ replay actions.
    - VIEWER (level 1): Read-only inspection of executions, traces, and approvals.
    """

    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"

    @property
    def level(self) -> int:
        """Numeric rank in role hierarchy."""
        hierarchy = {
            UserRole.VIEWER: 1,
            UserRole.OPERATOR: 2,
            UserRole.ADMIN: 3,
        }
        return hierarchy[self]

    def satisfies(self, required: UserRole | str) -> bool:
        """Check if this role satisfies or exceeds the required role.

        Supports hierarchy inheritance:
        - ADMIN satisfies ADMIN, OPERATOR, VIEWER.
        - OPERATOR satisfies OPERATOR, VIEWER.
        - VIEWER satisfies only VIEWER.
        """
        try:
            target = required if isinstance(required, UserRole) else UserRole(str(required).lower())
        except ValueError:
            return False
        return self.level >= target.level
