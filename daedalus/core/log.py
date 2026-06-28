"""Structured logging configuration for DAEDALUS.

Configures ``structlog`` for two modes:

* **Console** (CLI): coloured, human-readable output
* **JSON** (API / MCP server): machine-readable structured logs

Usage::

    from daedalus.core.log import configure_logging
    import structlog

    configure_logging(level="INFO", json=False)
    log = structlog.get_logger("daedalus")
    log.info("starting", version="0.1.0")
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog


def configure_logging(level: str = "INFO", *, json_mode: bool = False) -> None:
    """Configure structlog for DAEDALUS.

    Parameters
    ----------
    level:
        Log level: ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``, ``CRITICAL``.
    json_mode:
        If True, emit JSON logs (for server/agent contexts).
        If False, emit coloured console logs (for CLI).
    """
    processors: list[Callable[..., Any]] = [
        structlog.stdlib.add_log_level,
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if json_mode:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
