from __future__ import annotations

import os

from devx.config import Settings


def configure_tracing(settings: Settings) -> dict[str, str]:
    """Return safe opt-in tracing environment settings without exposing credentials."""
    if not settings.tracing_enabled:
        return {}
    values = {"LANGCHAIN_TRACING_V2": "true"}
    project = os.getenv("LANGSMITH_PROJECT") or os.getenv("LANGCHAIN_PROJECT", "devx-coding-agent")
    values["LANGCHAIN_PROJECT"] = project
    # LangChain reads the API key from the process environment; never copy it into logs/state.
    os.environ.update(values)
    return values