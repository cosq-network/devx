from __future__ import annotations

import json
import logging
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any


_correlation_id: threading.local = threading.local()


def get_correlation_id() -> str:
    """Get the current correlation ID, generating one if needed."""
    if not hasattr(_correlation_id, "value"):
        _correlation_id.value = str(uuid.uuid4())[:8]
    return _correlation_id.value


def set_correlation_id(cid: str | None = None) -> str:
    """Set the correlation ID for the current thread."""
    _correlation_id.value = cid or str(uuid.uuid4())[:8]
    return _correlation_id.value


def clear_correlation_id() -> None:
    """Clear the correlation ID for the current thread."""
    if hasattr(_correlation_id, "value"):
        del _correlation_id.value


@contextmanager
def correlation_context(cid: str | None = None):
    """Context manager for correlation ID."""
    old = getattr(_correlation_id, "value", None)
    _correlation_id.value = cid or str(uuid.uuid4())[:8]
    try:
        yield _correlation_id.value
    finally:
        if old is not None:
            _correlation_id.value = old
        else:
            clear_correlation_id()


class JSONFormatter(logging.Formatter):
    """JSON log formatter with correlation ID support."""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": get_correlation_id(),
        }
        
        # Add extra fields from record
        for key, value in record.__dict__.items():
            if key not in {"name", "msg", "args", "created", "filename", "funcName", "levelname", "levelno", "lineno", "module", "msecs", "message", "name", "pathname", "process", "processName", "relativeCreated", "thread", "threadName", "exc_info", "exc_text", "stack_info"}:
                log_data[key] = value
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_data, default=str)


def configure_logging(level: int = logging.INFO, json_output: bool = False) -> None:
    """Configure structured logging."""
    handler = logging.StreamHandler(sys.stdout)
    
    if json_output:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(correlation_id)s | %(message)s",
            datefmt="%H:%M:%S"
        ))
        # Add correlation ID to default formatter
        old_factory = logging.getLogRecordFactory()
        def record_factory(*args, **kwargs):
            record = old_factory(*args, **kwargs)
            record.correlation_id = get_correlation_id()
            return record
        logging.setLogRecordFactory(record_factory)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers = [handler]


def get_logger(name: str) -> logging.Logger:
    """Get a logger with correlation ID support."""
    return logging.getLogger(name)


class StructuredLogger:
    """Structured logger with typed methods."""
    
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
    
    def _log(self, level: int, message: str, **kwargs: Any) -> None:
        extra = {"correlation_id": get_correlation_id()}
        extra.update(kwargs)
        self.logger.log(level, message, extra=extra)
    
    def debug(self, message: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, message, **kwargs)
    
    def info(self, message: str, **kwargs: Any) -> None:
        self._log(logging.INFO, message, **kwargs)
    
    def warning(self, message: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, message, **kwargs)
    
    def error(self, message: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, message, **kwargs)
    
    def critical(self, message: str, **kwargs: Any) -> None:
        self._log(logging.CRITICAL, message, **kwargs)
    
    def exception(self, message: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, message, **kwargs)


# Global structured logger
logger = StructuredLogger("devx")