from datetime import UTC, datetime


def require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must include timezone information")
    return value


def assume_utc(value: datetime) -> datetime | None:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
