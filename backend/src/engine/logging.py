"""Structured JSON logging."""

import logging
import sys

import structlog

from engine.config import LogLevel


def configure_logging(level: LogLevel = "INFO") -> None:
    """Configure structlog to write one JSON object per line to stdout."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level],
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
    )
