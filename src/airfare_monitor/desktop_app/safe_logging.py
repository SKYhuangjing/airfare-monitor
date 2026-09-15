"""Bounded desktop logs with redaction before text reaches disk."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

from ..app_paths import AppPaths


_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_USER_PATH = re.compile(r"\b[A-Z]:\\Users\\[^\\\s]+", re.IGNORECASE)
_ASSIGNMENT = re.compile(
    r"\b(password|passwd|smtp_secret|authorization|cookie|token|bella|queryid|session_parameter)"
    r"\s*[:=]\s*[^\s,;&]+",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\bBearer\s+[^\s,;&]+", re.IGNORECASE)
_MAX_LOG_BYTES = 2_000_000
_BACKUPS = 5


def redact_text(value: str) -> str:
    """Mask likely secrets and personal addresses in formatted logs/reports."""
    value = _URL.sub("[url removed]", value)
    value = _EMAIL.sub("[email removed]", value)
    value = _USER_PATH.sub("[user path]", value)
    value = _ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[redacted]", value)
    return _BEARER.sub("Bearer [redacted]", value)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def _handler(path) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        path, maxBytes=_MAX_LOG_BYTES, backupCount=_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.setLevel(logging.INFO)
    return handler


def configure_desktop_logging(paths: AppPaths) -> None:
    """Configure app and collection logs once; do not attach raw stderr handlers."""
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    for name, filename in (
        ("airfare_monitor", "app.log"),
        ("airfare_monitor.service", "collection.log"),
    ):
        logger = logging.getLogger(name)
        wanted = str((paths.logs_dir / filename).resolve())
        for item in list(logger.handlers):
            if isinstance(item, RotatingFileHandler) and item.baseFilename != wanted:
                logger.removeHandler(item)
                item.close()
        if not any(
            isinstance(item, RotatingFileHandler) and item.baseFilename == wanted
            for item in logger.handlers
        ):
            logger.addHandler(_handler(wanted))
        logger.setLevel(logging.INFO)
    logging.getLogger("airfare_monitor").propagate = False


def close_desktop_logging() -> None:
    """Release Windows file handles before smoke-test cleanup or desktop exit."""
    for name in ("airfare_monitor.service", "airfare_monitor"):
        logger = logging.getLogger(name)
        for item in list(logger.handlers):
            if isinstance(item, RotatingFileHandler):
                logger.removeHandler(item)
                item.close()


def write_startup_failure(paths: AppPaths, exc: Exception):
    """Record a bounded, redacted traceback for windowed-EXE startup failures."""
    path = paths.logs_dir / "startup.log"
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    handler = _handler(path)
    try:
        record = logging.LogRecord(
            "airfare_monitor.startup", logging.ERROR, __file__, 0,
            f"startup failed: {type(exc).__name__}", (),
            (type(exc), exc, exc.__traceback__),
        )
        handler.handle(record)
    finally:
        handler.close()
    return path
