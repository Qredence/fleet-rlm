"""Structured logging configuration for fleet-rlm."""

from __future__ import annotations

import logging
import sys
import typing

import structlog


def configure_logging(
    level: int | str = logging.INFO,
    json_format: bool = False,
) -> None:
    """Configure structured logging for the application.

    Args:
        level: Logging level (e.g., logging.INFO, "DEBUG").
        json_format: Whether to output logs in JSON format (ideal for production).
            If False, uses ConsoleRenderer for pretty colored output.
    """
    if isinstance(level, str):
        level = level.upper()

    shared_processors: list[typing.Callable] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if json_format:
        processors = shared_processors + [
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(),
        ]

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging to use structlog
    formatter = structlog.stdlib.ProcessorFormatter(
        # These run solely on log entries that originate from structlog.stdlib.get_logger()
        foreign_pre_chain=shared_processors,
        # These run on ALL log entries (including third-party modules)
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer()
            if json_format
            else structlog.dev.ConsoleRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # Silence noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
