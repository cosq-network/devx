"""Tests for the observability layer: structured logging, health checks, and tracing setup.

Covers ``devx.observability`` (the entrypoint used by the CLI) and the canonical
``devx.observability.health`` module. All offline; no network or services.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

from devx import observability as obs
from devx.config import Settings
from devx.observability import health as health_mod


# The pristine factory, captured before any test installs a correlation-ID
# record factory via ``configure_logging`` (CLI tests never restore it).
_DEFAULT_RECORD_FACTORY = logging.getLogRecordFactory()


@pytest.fixture(autouse=True)
def snapshot_logging():
    """Isolate global logging state: root handlers/level, record factory,
    noisy-logger levels, and the langsmith logger tracing sets to INFO."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    noisy = ["httpx", "httpcore", "langchain", "langgraph", "urllib3", "langsmith"]
    saved_noisy = {name: logging.getLogger(name).level for name in noisy}
    logging.setLogRecordFactory(_DEFAULT_RECORD_FACTORY)
    yield
    logging.setLogRecordFactory(_DEFAULT_RECORD_FACTORY)
    obs.clear_correlation_id()
    root.handlers = saved_handlers
    root.setLevel(saved_level)
    for name, level in saved_noisy.items():
        logging.getLogger(name).setLevel(level)


def make_log_record(level=logging.INFO, message="hello %s", args=("world",), exc_info=None):
    return logging.LogRecord("devx.test", level, __file__, 42, message, args, exc_info)


class TestJsonFormatter:
    """The packaged ``JSONFormatter`` (correlation-ID aware JSON logs)."""

    def test_default_includes_timestamp_level_and_message(self):
        payload = json.loads(obs.JSONFormatter().format(make_log_record()))
        assert payload["logger"] == "devx.test"
        assert payload["message"] == "hello world"
        assert payload["level"] == "INFO"
        assert "timestamp" in payload
        assert "correlation_id" in payload

    def test_extra_fields_are_included(self):
        record = make_log_record()
        record.request_id = "req-1"
        payload = json.loads(obs.JSONFormatter().format(record))
        assert payload["request_id"] == "req-1"

    def test_exception_info_is_formatted(self):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            record = make_log_record(exc_info=sys.exc_info())
        payload = json.loads(obs.JSONFormatter().format(record))
        assert "boom" in payload["exception"]

    def test_serializes_non_json_values(self):
        record = make_log_record()
        record.extra_data = {"nested": [1, 2]}
        payload = json.loads(obs.JSONFormatter().format(record))
        assert payload["extra_data"] == {"nested": [1, 2]}


class TestPackageSurface:
    """The package re-exports the live logging/correlation/metrics/health APIs."""

    def test_structured_logger_re_exported(self, caplog):
        with caplog.at_level(logging.INFO, logger="devx.pkg.test"):
            obs.StructuredLogger("devx.pkg.test").info("pkg line", task_id="t1")
        assert caplog.records[0].getMessage() == "pkg line"
        assert caplog.records[0].task_id == "t1"
        assert caplog.records[0].correlation_id

    def test_get_logger_is_logging_getlogger(self):
        assert obs.get_logger("devx.surface.test") is logging.getLogger("devx.surface.test")

    def test_correlation_helpers_re_exported(self):
        obs.set_correlation_id("fixed-cid")
        assert obs.get_correlation_id() == "fixed-cid"
        with obs.correlation_context("inner") as cid:
            assert cid == "inner"
        assert obs.get_correlation_id() == "fixed-cid"
        obs.clear_correlation_id()

    def test_metrics_and_health_re_exports(self):
        assert obs.MetricsCollector is not None
        assert obs.Metrics is not None
        assert callable(obs.increment_counter)
        assert callable(obs.get_overall_health)
        assert callable(obs.configure_tracing)
        assert obs.HealthCheckResult is health_mod.HealthCheckResult


class TestConfigureLogging:
    def test_json_output_writes_structured_lines(self, capsys):
        obs.configure_logging(json_output=True, level=logging.INFO)
        logging.getLogger("devx.health.test").info("structured message")
        captured = capsys.readouterr().out
        payload = json.loads(captured.strip().splitlines()[-1])
        assert payload["message"] == "structured message"
        assert payload["level"] == "INFO"
        assert "correlation_id" in payload

    def test_text_output_formats_with_correlation_id(self, capsys):
        obs.configure_logging(json_output=False, level=logging.INFO)
        obs.set_correlation_id("txt-7")
        logging.getLogger("devx.health.test").info("plain message")
        captured = capsys.readouterr().out
        assert "plain message" in captured
        assert "txt-7" in captured

    def test_level_filters_lower_severity(self, capsys):
        obs.configure_logging(json_output=False, level=logging.WARNING)
        logging.getLogger("devx.health.test").info("dropped")
        logging.getLogger("devx.health.test").warning("kept")
        captured = capsys.readouterr().out
        assert "dropped" not in captured
        assert "kept" in captured

    def test_replaces_existing_root_handlers(self, capsys):
        logging.getLogger().addHandler(logging.NullHandler())
        obs.configure_logging(json_output=False)
        root = logging.getLogger()
        assert len(root.handlers) == 1
        capsys.readouterr()


class TestConfigureTracing:
    def test_disabled_is_noop(self):
        obs.configure_tracing(Settings(tracing_enabled=False))
        assert logging.getLogger("langsmith").level == logging.NOTSET

    def test_enabled_with_langsmith_installed(self, monkeypatch):
        monkeypatch.setattr(
            "importlib.util.find_spec", lambda name: object() if name == "langsmith" else None
        )
        obs.configure_tracing(Settings(tracing_enabled=True))
        assert logging.getLogger("langsmith").level == logging.INFO

    def test_enabled_without_langsmith_warns(self, monkeypatch, caplog):
        monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
        with caplog.at_level(logging.WARNING, logger="devx.observability"):
            obs.configure_tracing(Settings(tracing_enabled=True))
        assert "langsmith package not installed" in caplog.text

    def test_disabled_never_warns(self, monkeypatch, caplog):
        monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
        with caplog.at_level(logging.WARNING, logger="devx.observability"):
            obs.configure_tracing(Settings(tracing_enabled=False))
        assert "langsmith package not installed" not in caplog.text


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        workspace=tmp_path / "ws",
        session_db=tmp_path / "sessions.db",
        cache_db=tmp_path / "cache.db",
        provider="openai",
        api_key="sk-real-key-abcdef",
    )
    values.update(overrides)
    return Settings(**values)


HEALTH_SOURCES = [obs.run_health_checks, health_mod.run_health_checks]


class TestHealthChecks:
    @pytest.mark.parametrize("run_health", HEALTH_SOURCES)
    def test_all_components_healthy(self, tmp_path, run_health):
        (tmp_path / "ws").mkdir()
        results = run_health(make_settings(tmp_path))
        by_component = {r.component: r.status for r in results}
        assert by_component == {
            "workspace": "healthy",
            "session_db": "healthy",
            "cache_db": "healthy",
            "llm_provider": "healthy",
        }

    def test_workspace_missing_is_unhealthy(self, tmp_path):
        results = obs.run_health_checks(make_settings(tmp_path))
        workspace = next(r for r in results if r.component == "workspace")
        assert workspace.status == "unhealthy"
        assert "Workspace does not exist" in workspace.details["error"]

    def test_session_db_broken_is_unhealthy(self, tmp_path):
        # A directory where the sqlite database is expected makes it unusable.
        (tmp_path / "sessions.db").mkdir()
        results = obs.run_health_checks(make_settings(tmp_path))
        session_db = next(r for r in results if r.component == "session_db")
        assert session_db.status == "unhealthy"

    def test_cache_db_broken_is_unhealthy(self, tmp_path):
        (tmp_path / "cache.db").mkdir()
        results = obs.run_health_checks(make_settings(tmp_path))
        cache_db = next(r for r in results if r.component == "cache_db")
        assert cache_db.status == "unhealthy"

    def test_ollama_with_base_url_healthy(self, tmp_path):
        (tmp_path / "ws").mkdir()
        results = obs.run_health_checks(
            make_settings(tmp_path, provider="ollama", base_url="http://localhost:11434")
        )
        llm = next(r for r in results if r.component == "llm_provider")
        assert llm.status == "healthy"
        assert llm.details["base_url"] == "http://localhost:11434"

    def test_ollama_without_base_url_degraded(self, tmp_path):
        results = obs.run_health_checks(make_settings(tmp_path, provider="ollama", base_url=""))
        llm = next(r for r in results if r.component == "llm_provider")
        assert llm.status == "degraded"

    def test_placeholder_key_degraded(self, tmp_path):
        results = obs.run_health_checks(make_settings(tmp_path, api_key="sk-placeholder"))
        llm = next(r for r in results if r.component == "llm_provider")
        assert llm.status == "degraded"

    def test_latency_measured_for_each_component(self, tmp_path):
        (tmp_path / "ws").mkdir()
        results = obs.run_health_checks(make_settings(tmp_path))
        assert results
        assert all(r.latency_ms is not None for r in results)


class TestOverallHealth:
    def test_healthy_when_all_healthy(self):
        results = [obs.HealthCheckResult(component="a", status="healthy")]
        assert obs.get_overall_health(results) == "healthy"

    def test_degraded_when_any_degraded(self):
        results = [
            obs.HealthCheckResult(component="a", status="healthy"),
            obs.HealthCheckResult(component="b", status="degraded"),
        ]
        assert obs.get_overall_health(results) == "degraded"

    def test_unhealthy_takes_priority(self):
        results = [
            obs.HealthCheckResult(component="a", status="unhealthy"),
            obs.HealthCheckResult(component="b", status="degraded"),
        ]
        assert obs.get_overall_health(results) == "unhealthy"

    def test_empty_result_list_is_healthy(self):
        assert obs.get_overall_health([]) == "healthy"


def test_health_check_result_defaults():
    result = obs.HealthCheckResult(component="x", status="healthy")
    assert result.details == {}
    assert result.latency_ms is None