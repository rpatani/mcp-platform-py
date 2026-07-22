"""structlog wiring: JSON logs to stdout (HTTP) / stderr (stdio), with secret redaction."""

from __future__ import annotations

import sys
from typing import Literal, cast

import structlog
from structlog.types import EventDict

_REDACTED = "***REDACTED***"
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "x-api-key",
        "openweathermap_api_key",
    }
)


def _redact_secrets(logger: object, method_name: str, event_dict: EventDict) -> EventDict:
    for key in event_dict:
        if key.lower() in _SECRET_KEYS:
            event_dict[key] = _REDACTED
    return event_dict


def create_logger(
    *,
    service: str,
    version: str,
    transport: Literal["stdio", "http"],
    level: str = "info",
) -> structlog.BoundLogger:
    """Configure structlog and return a logger bound with service/version.

    stdout is reserved for the JSON-RPC wire in the stdio transport, so stdio
    logs go to stderr; HTTP logs go to stdout (CLAUDE.md §6).
    """
    stream = sys.stderr if transport == "stdio" else sys.stdout

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_secrets,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level.upper()),
        logger_factory=structlog.PrintLoggerFactory(file=stream),
        cache_logger_on_first_use=False,
    )

    return cast("structlog.BoundLogger", structlog.get_logger(service=service, version=version))
