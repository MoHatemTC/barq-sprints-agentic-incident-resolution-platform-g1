"""JSON encoding for PostgreSQL JSON/JSONB columns.

PostgreSQL rejects the NUL character (``\\u0000``) in JSON text. Model output, incident
text and tool responses can contain it, and before this encoder one NUL in an approval
brief made the workflow-state insert fail, the run end as an unclassified failure, and
ServiceNow keep an "awaiting approval" that the backend no longer had. Every engine uses
this serializer, so no write path can reintroduce the problem.
"""

from __future__ import annotations

import json
from typing import Any

_NUL = "\x00"


def strip_nul(value: Any) -> Any:
    """Return ``value`` with NUL removed from every string, keys included."""
    if isinstance(value, str):
        return value.replace(_NUL, "") if _NUL in value else value
    if isinstance(value, dict):
        return {strip_nul(k): strip_nul(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [strip_nul(item) for item in value]
    return value


def pg_json_dumps(value: Any) -> str:
    """``json.dumps`` for PostgreSQL: NUL characters removed first."""
    return json.dumps(strip_nul(value))


__all__ = ["pg_json_dumps", "strip_nul"]
