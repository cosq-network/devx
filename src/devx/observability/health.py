from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from devx.config import Settings


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