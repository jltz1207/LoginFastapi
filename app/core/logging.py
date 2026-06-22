"""
Structured logging setup using structlog.

Call setup_logging() once at application startup (top of main.py / celery_app.py).
Use get_logger(__name__) anywhere in the codebase.

Environment variables (override Settings defaults):
  LOG_LEVEL  = DEBUG | INFO | WARNING | ERROR   (default: INFO)
  LOG_FORMAT = console | json                   (default: console)
"""
import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict

from app.core.config import app_env, settings

_NOISY_LOGGERS = (
    "sqlalchemy.engine",
    "sqlalchemy.pool",
    "sqlalchemy.dialects",
    "httpx",
    "httpcore",
    "chromadb",
    "langchain",
    "langchain_core",
    "langchain_community",
    "openai",
    "google",
    "uvicorn.access",  # replaced by RequestLoggerMiddleware
)

_configured = False


# --------------------------------------------------------------------------- #
# Custom processors
# --------------------------------------------------------------------------- #

def _add_app_context(_: Any, __: str, event_dict: EventDict) -> EventDict:
    event_dict["environment"] = app_env
    return event_dict


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def setup_logging() -> None:
    """
    Configure structlog + stdlib root logger.
    Safe to call multiple times (no-op after the first call).
    """
    global _configured
    if _configured:
        return
    _configured = True

    log_level_str = settings.LOG_LEVEL.upper()
    log_format = settings.LOG_FORMAT.lower()
    level = getattr(logging, log_level_str, logging.INFO)

    # Processors shared by both structlog-native and stdlib-bridged records.
    # Order matters: context vars must merge first; renderer is NOT here.
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_app_context,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
    ]

    # Final processor chain for the stdlib formatter (renderer goes here).
    if log_format == "json":
        final_processors: list = [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.ExceptionRenderer(),   # serialize exc to dict
            structlog.processors.JSONRenderer(),
        ]
    else:
        final_processors = [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=True),  # handles exc natively
        ]

    # Bridge: stdlib records flow through ProcessorFormatter → same chain.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=final_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Tell structlog to hand off to the stdlib handler via wrap_for_formatter.
    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Silence noisy third-party loggers (keep WARNING+ only).
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Return a structlog logger bound to *name*.

    Usage:
        logger = get_logger(__name__)
        logger.info("user.created", user_id=str(user.id))
    """
    return structlog.get_logger(name)
