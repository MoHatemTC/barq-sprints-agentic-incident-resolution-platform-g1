from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


def values_equal(requested: Any, persisted: Any) -> bool:
    req_bool = _normalize_bool(requested)
    per_bool = _normalize_bool(persisted)
    if req_bool is not None and per_bool is not None:
        return req_bool == per_bool

    try:
        return Decimal(str(requested)) == Decimal(str(persisted))
    except (InvalidOperation, ValueError, TypeError):
        pass

    if isinstance(persisted, str):
        try:
            return datetime.fromisoformat(str(requested)) == datetime.fromisoformat(persisted)
        except ValueError:
            pass
    elif isinstance(persisted, datetime):
        try:
            return datetime.fromisoformat(str(requested)) == persisted
        except ValueError:
            pass

    return requested == persisted


def _normalize_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "t"}:
            return True
        if normalized in {"false", "0", "no", "f"}:
            return False
    return None
