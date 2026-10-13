from __future__ import annotations

import importlib.util
import logging
import os

from devx.config import Settings


def configure_tracing(settings: Settings) -> dict[str, str]:
    """Configure LangSmith tracing if enabled.

    Mutates the process environment so downstream LangChain/LangSmith clients
    pick the values up (``LANGSMITH_TRACING`` / ``LANGSMITH_PROJECT``), and
    returns the values applied. Credentials are never copied into logs, state,
    or the returned dict. Safe to call repeatedly.
    """
    if not settings.tracing_enabled:
        return {}
    values = {"LANGSMITH_TRACING": "true"}
    project = os.getenv("LANGSMITH_PROJECT") or os.getenv("LANGCHAIN_PROJECT") or "devx-coding-agent"
    values["LANGSMITH_PROJECT"] = project
    try:
        if importlib.util.find_spec("langsmith") is not None:
            logging.getLogger("langsmith").setLevel(logging.INFO)
        else:
            logging.getLogger(__name__).warning(
                "LangSmith tracing requested but langsmith package not installed"
            )
    except Exception:
        logging.getLogger(__name__).warning(
            "LangSmith tracing requested but langsmith package not installed"
        )
    os.environ.update(values)
    return values