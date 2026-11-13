"""Structured logging: JSON lines for machines, concise Rich output for humans.

Events carry context fields (``run_id``, ``stage``, ``source``, ``batch``, ``documents``,
``elapsed_s``, ``rss_mb``). Callers must never pass document text, PII, or secret values;
privacy stages log counts and types only.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any, Final

from rich.console import Console
from rich.logging import RichHandler

LOGGER_NAME: Final = "dataforge"
_RESERVED: Final = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": dt.datetime.fromtimestamp(record.created, tz=dt.UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def configure_logging(level: str = "INFO", *, console: Console | None = None, quiet: bool = False) -> None:
    """Install the human-readable console handler (idempotent)."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        if isinstance(handler, RichHandler):
            logger.removeHandler(handler)
    if quiet:
        return
    handler = RichHandler(
        console=console or Console(stderr=True),
        show_path=False,
        show_time=False,
        markup=False,
        rich_tracebacks=False,
    )
    handler.setLevel(level.upper())
    logger.addHandler(handler)


class JsonFileLog:
    """Context manager attaching a JSON-lines file handler for one run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handler: logging.FileHandler | None = None

    def __enter__(self) -> JsonFileLog:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handler = logging.FileHandler(self.path, encoding="utf-8")
        self.handler.setLevel(logging.DEBUG)
        self.handler.setFormatter(JsonFormatter())
        logger = logging.getLogger(LOGGER_NAME)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(self.handler)
        return self

    def __exit__(self, *_: object) -> None:
        if self.handler is not None:
            logging.getLogger(LOGGER_NAME).removeHandler(self.handler)
            self.handler.close()
            self.handler = None
