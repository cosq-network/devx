from __future__ import annotations

import logging
import importlib.util

from devx.config import Settings


def configure_tracing(settings: Settings) -> None:
    """Configure LangSmith tracing if enabled."""
    if not settings.tracing_enabled:
        return

    try:
        if importlib.util.find_spec("langsmith") is not None:
            # LangSmith is configured via environment variables:
            # LANGSMITH_TRACING, LANGSMITH_API_KEY, LANGSMITH_PROJECT
            logging.getLogger("langsmith").setLevel(logging.INFO)
        else:
            logging.getLogger(__name__).warning("LangSmith tracing requested but langsmith package not installed")
    except Exception:
        logging.getLogger(__name__).warning("LangSmith tracing requested but langsmith package not installed")