"""Tests for both tracing configuration entrypoints.

``devx.tracing.configure_tracing`` maps settings to LangChain/LangSmith
environment variables; ``devx.observability.tracing.configure_tracing``
scopes the LangSmith logger. Both are offline.
"""

from __future__ import annotations

import logging
import os

import pytest

from devx.config import Settings
from devx import tracing as top_tracing
from devx.observability import tracing as observability_tracing


@pytest.fixture(autouse=True)
def isolate_langsmith_logger():
    """Restore the langsmith logger level after each tracing test."""
    logger = logging.getLogger("langsmith")
    before = logger.level
    yield
    logger.setLevel(before)


class TestTopLevelTracing:
    def test_disabled_returns_empty_and_touches_nothing(self, monkeypatch):
        monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
        monkeypatch.delenv("LANGCHAIN_PROJECT", raising=False)
        result = top_tracing.configure_tracing(Settings(tracing_enabled=False))
        assert result == {}
        assert "LANGCHAIN_TRACING_V2" not in os.environ
        assert "LANGCHAIN_PROJECT" not in os.environ

    def test_enabled_sets_default_project(self, monkeypatch):
        monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
        monkeypatch.delenv("LANGCHAIN_PROJECT", raising=False)
        result = top_tracing.configure_tracing(Settings(tracing_enabled=True))
        assert result == {
            "LANGCHAIN_TRACING_V2": "true",
            "LANGCHAIN_PROJECT": "devx-coding-agent",
        }
        assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
        assert os.environ["LANGCHAIN_PROJECT"] == "devx-coding-agent"
        monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
        monkeypatch.delenv("LANGCHAIN_PROJECT", raising=False)

    def test_langsmith_project_wins(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_PROJECT", "my-langsmith-project")
        result = top_tracing.configure_tracing(Settings(tracing_enabled=True))
        assert result["LANGCHAIN_PROJECT"] == "my-langsmith-project"
        monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
        monkeypatch.delenv("LANGCHAIN_PROJECT", raising=False)

    def test_langchain_project_fallback(self, monkeypatch):
        monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
        monkeypatch.setenv("LANGCHAIN_PROJECT", "legacy-project")
        result = top_tracing.configure_tracing(Settings(tracing_enabled=True))
        assert result["LANGCHAIN_PROJECT"] == "legacy-project"
        monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)


class TestObservabilityTracing:
    def test_disabled_is_noop(self):
        observability_tracing.configure_tracing(Settings(tracing_enabled=False))
        assert logging.getLogger("langsmith").level == logging.NOTSET

    def test_enabled_with_langsmith_installed(self, monkeypatch):
        monkeypatch.setattr(
            "importlib.util.find_spec", lambda name: object() if name == "langsmith" else None
        )
        observability_tracing.configure_tracing(Settings(tracing_enabled=True))
        assert logging.getLogger("langsmith").level == logging.INFO

    def test_enabled_without_langsmith_warns(self, monkeypatch, caplog):
        monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
        with caplog.at_level(logging.WARNING, logger="devx.observability.tracing"):
            observability_tracing.configure_tracing(Settings(tracing_enabled=True))
        assert "langsmith package not installed" in caplog.text