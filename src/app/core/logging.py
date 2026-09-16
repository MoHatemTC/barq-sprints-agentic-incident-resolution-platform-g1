from __future__ import annotations

import logging

import structlog
from structlog.types import EventDict, Processor, WrappedLogger

from app.core.config import Environment

SENSITIVE_KEYS = {
    "authorization",
    "token",
    "access_token",
    "refresh_token",
    "client_secret",
    "password",
}
REDACTED = "***REDACTED***"


def _redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            k: REDACTED if str(k).lower() in SENSITIVE_KEYS else _redact(v)
            for k, v in value.items()
        }
    return value


def redact_sensitive_data(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    for key, value in event_dict.items():
        event_dict[key] = REDACTED if str(key).lower() in SENSITIVE_KEYS else _redact(value)
    return event_dict


def configure_logging(
    *, environment: Environment = Environment.DEVELOPMENT, log_level: str = "INFO"
) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    processors: list[Processor] = [
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive_data,
    ]

    if environment == Environment.PRODUCTION:
        processors += [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors.append(
            structlog.dev.ConsoleRenderer(exception_formatter=structlog.dev.plain_traceback)
        )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[log_level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
