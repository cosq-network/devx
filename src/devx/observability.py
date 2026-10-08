from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from devx.config import Settings


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def __init__(self, include_timestamp: bool = True, include_level: bool = True):
        super().__init__()
        self.include_timestamp = include_timestamp
        self.include_level = include_level

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "logger": record.name,
            "message": record.getMessage(),
        }

        if self.include_timestamp:
            log_entry["timestamp"] = datetime.fromtimestamp(record.created).isoformat()

        if self.include_level:
            log_entry["level"] = record.levelname

        # Add extra fields
        for key, value in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "created", "filename", "funcName",
                "levelname", "levelno", "lineno", "module", "msecs",
                "message", "msg", "name", "pathname", "process",
                "processName", "relativeCreated", "thread", "threadName",
                "exc_info", "exc_text", "stack_info", "getMessage"
            }:
                log_entry[key] = value

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


def configure_logging(settings: Settings | None = None, *, level: int = logging.INFO, json_format: bool = False) -> None:
    """Configure application logging."""
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)

    if json_format:
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))

    root_logger.addHandler(handler)

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("langchain").setLevel(logging.WARNING)
    logging.getLogger("langgraph").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


@dataclass
class LogContext:
    """Context manager for adding structured context to logs."""
    logger: logging.Logger
    context: dict[str, Any] = field(default_factory=dict)

    def __enter__(self) -> LogContext:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    def bind(self, **kwargs: Any) -> LogContext:
        """Add context to this logger."""
        self.context.update(kwargs)
        return self

    def info(self, message: str, **kwargs: Any) -> None:
        self.logger.info(message, extra={**self.context, **kwargs})

    def debug(self, message: str, **kwargs: Any) -> None:
        self.logger.debug(message, extra={**self.context, **kwargs})

    def warning(self, message: str, **kwargs: Any) -> None:
        self.logger.warning(message, extra={**self.context, **kwargs})

    def error(self, message: str, **kwargs: Any) -> None:
        self.logger.error(message, extra={**self.context, **kwargs})

    def exception(self, message: str, **kwargs: Any) -> None:
        self.logger.exception(message, extra={**self.context, **kwargs})


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    return logging.getLogger(name)


def get_structured_logger(name: str) -> LogContext:
    """Get a logger with structured context support."""
    return LogContext(logging.getLogger(name))


@contextmanager
def log_duration(logger: logging.Logger, operation: str, **context: Any):
    """Context manager to log operation duration."""
    start = time.monotonic()
    logger.info(f"Starting {operation}", extra=context)
    try:
        yield
    except Exception as exc:
        duration = time.monotonic() - start
        logger.error(
            f"Failed {operation}",
            extra={**context, "duration_seconds": duration, "error": str(exc)},
        )
        raise
    else:
        duration = time.monotonic() - start
        logger.info(
            f"Completed {operation}",
            extra={**context, "duration_seconds": duration},
        )


def configure_tracing(settings: Settings) -> None:
    """Configure LangSmith tracing if enabled."""
    if not settings.tracing_enabled:
        return

    try:
        import importlib.util
        if importlib.util.find_spec("langsmith") is not None:
            # LangSmith is configured via environment variables:
            # LANGSMITH_TRACING, LANGSMITH_API_KEY, LANGSMITH_PROJECT
            logging.getLogger("langsmith").setLevel(logging.INFO)
        else:
            logging.getLogger(__name__).warning("LangSmith tracing requested but langsmith package not installed")
    except Exception:
        logging.getLogger(__name__).warning("LangSmith tracing requested but langsmith package not installed")


# Health check types
@dataclass
class HealthCheckResult:
    component: str
    status: str  # "healthy", "degraded", "unhealthy"
    details: dict[str, Any] = field(default_factory=dict)
    latency_ms: float | None = None


def run_health_checks(settings: Settings) -> list[HealthCheckResult]:
    """Run basic health checks and return results."""
    results = []

    # Check workspace
    start = time.monotonic()
    try:
        workspace = settings.workspace
        if workspace.exists() and workspace.is_dir():
            results.append(HealthCheckResult(
                component="workspace",
                status="healthy",
                details={"path": str(workspace)},
                latency_ms=(time.monotonic() - start) * 1000,
            ))
        else:
            results.append(HealthCheckResult(
                component="workspace",
                status="unhealthy",
                details={"path": str(workspace), "error": "Workspace does not exist"},
                latency_ms=(time.monotonic() - start) * 1000,
            ))
    except Exception as exc:
        results.append(HealthCheckResult(
            component="workspace",
            status="unhealthy",
            details={"error": str(exc)},
            latency_ms=(time.monotonic() - start) * 1000,
        ))

    # Check session database
    start = time.monotonic()
    try:
        from devx.harness.session import SessionStore
        store = SessionStore(settings.session_db)
        store.list_sessions()
        results.append(HealthCheckResult(
            component="session_db",
            status="healthy",
            details={"path": str(settings.session_db)},
            latency_ms=(time.monotonic() - start) * 1000,
        ))
    except Exception as exc:
        results.append(HealthCheckResult(
            component="session_db",
            status="unhealthy",
            details={"path": str(settings.session_db), "error": str(exc)},
            latency_ms=(time.monotonic() - start) * 1000,
        ))

    # Check cache database
    start = time.monotonic()
    try:
        from devx.token_optim.cache import SemanticCache
        cache = SemanticCache(settings.cache_db)
        cache.get("health-check")
        results.append(HealthCheckResult(
            component="cache_db",
            status="healthy",
            details={"path": str(settings.cache_db)},
            latency_ms=(time.monotonic() - start) * 1000,
        ))
    except Exception as exc:
        results.append(HealthCheckResult(
            component="cache_db",
            status="unhealthy",
            details={"path": str(settings.cache_db), "error": str(exc)},
            latency_ms=(time.monotonic() - start) * 1000,
        ))

    # Check LLM provider connectivity (basic validation)
    start = time.monotonic()
    try:
        provider = settings.provider
        if provider == "ollama":
            # For Ollama, just check base_url format
            if settings.base_url:
                results.append(HealthCheckResult(
                    component="llm_provider",
                    status="healthy",
                    details={"provider": provider, "base_url": settings.base_url},
                    latency_ms=(time.monotonic() - start) * 1000,
                ))
            else:
                results.append(HealthCheckResult(
                    component="llm_provider",
                    status="degraded",
                    details={"provider": provider, "error": "No base URL configured"},
                    latency_ms=(time.monotonic() - start) * 1000,
                ))
        else:
            # For other providers, check API key
            if settings.api_key and settings.api_key != "sk-placeholder":
                results.append(HealthCheckResult(
                    component="llm_provider",
                    status="healthy",
                    details={"provider": provider},
                    latency_ms=(time.monotonic() - start) * 1000,
                ))
            else:
                results.append(HealthCheckResult(
                    component="llm_provider",
                    status="degraded",
                    details={"provider": provider, "error": "No API key configured"},
                    latency_ms=(time.monotonic() - start) * 1000,
                ))
    except Exception as exc:
        results.append(HealthCheckResult(
            component="llm_provider",
            status="unhealthy",
            details={"error": str(exc)},
            latency_ms=(time.monotonic() - start) * 1000,
        ))

    return results


def get_overall_health(results: list[HealthCheckResult]) -> str:
    """Determine overall health status from component results."""
    if any(r.status == "unhealthy" for r in results):
        return "unhealthy"
    if any(r.status == "degraded" for r in results):
        return "degraded"
    return "healthy"