"""Tests for the tracing configuration entrypoint.

``devx.observability.tracing.configure_tracing`` maps settings to
LangChain/LangSmith environment variables when tracing is enabled and scopes a
warning when the langsmith package is missing. All offline.
"""

from __future__ import annotations

import logging
import os

import pytest

from devx.config import Settings
from devx.observability import tracing as observability_tracing

_TRACING_ENV_KEYS = ("LANGSMITH_TRACING", "LANGSMITH_PROJECT", "LANGCHAIN_PROJECT")


@pytest.fixture(autouse=True)
def isolate_tracing_env():
    """Isolate tracing env vars and the langsmith logger for each test.

    ``configure_tracing`` writes straight to ``os.environ``; a later
    ``monkeypatch.delenv`` would capture the *mutated* value and restore it at
    teardown, leaking ``LANGSMITH_TRACING`` into other test modules. Snapshot and
    clear the real environment instead, then restore it afterwards.
    """
    logger = logging.getLogger("langsmith")
    before_level = logger.level
    saved = {key: os.environ[key] for key in _TRACING_ENV_KEYS if key in os.environ}
    for key in _TRACING_ENV_KEYS:
        os.environ.pop(key, None)
    yield
    for key in _TRACING_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ.update(saved)
    logger.setLevel(before_level)


class TestConfigureTracing:
    def test_disabled_returns_empty_and_touches_nothing(self):
        result = observability_tracing.configure_tracing(Settings(tracing_enabled=False))
        assert result == {}
        assert "LANGSMITH_TRACING" not in os.environ
        assert "LANGSMITH_PROJECT" not in os.environ

    def test_enabled_sets_default_project(self):
        result = observability_tracing.configure_tracing(Settings(tracing_enabled=True))
        assert result == {
            "LANGSMITH_TRACING": "true",
            "LANGSMITH_PROJECT": "devx-coding-agent",
        }
        assert os.environ["LANGSMITH_TRACING"] == "true"
        assert os.environ["LANGSMITH_PROJECT"] == "devx-coding-agent"

    def test_langsmith_project_wins(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_PROJECT", "my-langsmith-project")
        result = observability_tracing.configure_tracing(Settings(tracing_enabled=True))
        assert result["LANGSMITH_PROJECT"] == "my-langsmith-project"

    def test_langchain_project_fallback(self, monkeypatch):
        monkeypatch.setenv("LANGCHAIN_PROJECT", "legacy-project")
        result = observability_tracing.configure_tracing(Settings(tracing_enabled=True))
        assert result["LANGSMITH_PROJECT"] == "legacy-project"

    def test_enabled_with_langsmith_installed_sets_logger_level(self, monkeypatch):
        monkeypatch.setattr(
            "importlib.util.find_spec", lambda name: object() if name == "langsmith" else None
        )
        observability_tracing.configure_tracing(Settings(tracing_enabled=True))
        assert logging.getLogger("langsmith").level == logging.INFO

    def test_enabled_without_langsmith_warns(self, monkeypatch, caplog):
        monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
        with caplog.at_level(logging.WARNING, logger="devx.observability"):
            observability_tracing.configure_tracing(Settings(tracing_enabled=True))
        assert "langsmith package not installed" in caplog.text
