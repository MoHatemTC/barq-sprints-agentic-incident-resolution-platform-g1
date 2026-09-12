from datetime import datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
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


def parse_retry_after(value: str | None) -> int | float | None:
    if not value:
        return None

    try:
        return float(value)
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(value)
        now = datetime.now(retry_at.tzinfo)
        return max(0.0, (retry_at - now).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


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
