"""Tests for correlation-ID structured logging (``devx.observability.logging``)."""

from __future__ import annotations

import json
import logging
import sys

import pytest

from devx.observability import logging as devx_logging


# Capture the pristine factory at import time: CLI tests (via CliRunner) install
# a correlation-ID record factory through ``configure_logging`` and never restore
# it, so snapshots taken mid-run can be polluted already.
_DEFAULT_RECORD_FACTORY = logging.getLogRecordFactory()


@pytest.fixture(autouse=True)
def isolate_logging():
    root = logging.getLogger()
    handlers = root.handlers[:]
    level = root.level
    logging.setLogRecordFactory(_DEFAULT_RECORD_FACTORY)
    yield
    logging.setLogRecordFactory(_DEFAULT_RECORD_FACTORY)
    root.handlers = handlers
    root.setLevel(level)
    devx_logging.clear_correlation_id()


class TestCorrelationIds:
    def test_generates_and_returns_same_id(self):
        first = devx_logging.get_correlation_id()
        assert len(first) == 8
        assert devx_logging.get_correlation_id() == first

    def test_set_and_clear(self):
        devx_logging.set_correlation_id("abc-123")
        assert devx_logging.get_correlation_id() == "abc-123"
        devx_logging.clear_correlation_id()
        assert devx_logging.get_correlation_id() != "abc-123"

    def test_set_with_none_generates_new_id(self):
        first = devx_logging.get_correlation_id()
        generated = devx_logging.set_correlation_id()
        assert generated != first
        assert len(generated) == 8

    def test_correlation_context_restores_previous(self):
        devx_logging.set_correlation_id("outer")
        with devx_logging.correlation_context("inner") as cid:
            assert cid == "inner"
            assert devx_logging.get_correlation_id() == "inner"
        assert devx_logging.get_correlation_id() == "outer"

    def test_correlation_context_clears_when_none_before(self):
        devx_logging.clear_correlation_id()
        with devx_logging.correlation_context("inner"):
            pass
        assert devx_logging.get_correlation_id() != "inner"


class TestJsonFormatter:
    def test_formats_record_with_correlation_id(self):
        devx_logging.set_correlation_id("cid-42")
        record = logging.LogRecord("m", logging.INFO, __file__, 1, "hello", (), None)
        payload = json.loads(devx_logging.JSONFormatter().format(record))
        assert payload["message"] == "hello"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "m"
        assert payload["correlation_id"] == "cid-42"
        assert "timestamp" in payload

    def test_includes_extra_fields(self):
        record = logging.LogRecord("m", logging.WARNING, __file__, 1, "w", (), None)
        record.run_id = "r9"
        payload = json.loads(devx_logging.JSONFormatter().format(record))
        assert payload["run_id"] == "r9"
        assert payload["level"] == "WARNING"

    def test_includes_exception_details(self):
        try:
            raise ValueError("bad value")
        except ValueError:
            record = logging.LogRecord("m", logging.ERROR, __file__, 1, "e", (), sys.exc_info())
        payload = json.loads(devx_logging.JSONFormatter().format(record))
        assert "bad value" in payload["exception"]


class TestConfigureLogging:
    def test_json_output(self, capsys):
        devx_logging.configure_logging(json_output=True)
        logging.getLogger("devx.logging.test").info("json line")
        captured = capsys.readouterr().out
        payload = json.loads(captured.strip().splitlines()[-1])
        assert payload["message"] == "json line"
        assert "correlation_id" in payload

    def test_text_output_has_correlation_id(self, capsys):
        devx_logging.configure_logging(json_output=False)
        devx_logging.set_correlation_id("txt-55")
        logging.getLogger("devx.logging.test").info("text line")
        captured = capsys.readouterr().out
        assert "txt-55" in captured
        assert "text line" in captured

    def test_sets_root_level(self):
        devx_logging.configure_logging(level=logging.ERROR, json_output=False)
        assert logging.getLogger().level == logging.ERROR


class TestStructuredLogger:
    def test_log_methods_pass_extras(self, caplog):
        logger = devx_logging.StructuredLogger("devx.structured.test")
        with caplog.at_level(logging.DEBUG, logger="devx.structured.test"):
            logger.info("info msg", run_id="r1")
            logger.debug("debug msg")
            logger.warning("warn msg", code=7)
        assert [r.levelname for r in caplog.records] == ["INFO", "DEBUG", "WARNING"]
        assert caplog.records[0].run_id == "r1"
        assert caplog.records[0].correlation_id
        assert caplog.records[2].code == 7

    def test_error_and_critical_and_exception(self, caplog):
        logger = devx_logging.StructuredLogger("devx.structured.test")
        with caplog.at_level(logging.ERROR, logger="devx.structured.test"):
            logger.error("e")
            logger.critical("c")
            logger.exception("x")
        assert [r.levelname for r in caplog.records] == ["ERROR", "CRITICAL", "ERROR"]

    def test_module_level_logger_points_at_devx(self):
        assert devx_logging.logger.logger.name == "devx"