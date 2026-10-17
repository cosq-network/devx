"""Report and usage-receipt tests (``devx report``, ``devx usage --run --markdown``)."""

import json
from pathlib import Path

from typer.testing import CliRunner

from devx.cli.app import app
from devx.config import Settings
from devx.harness.changes import ChangeJournal, file_hash
from devx.harness.session import SessionStore
from devx.pricing import render_usage_report
from devx.report import build_report, render_report

runner = CliRunner()

RUN_ID = "run-abc"


def _fixture(tmp_path: Path) -> tuple[SessionStore, ChangeJournal, str]:
    db = tmp_path / "sessions.db"
    store = SessionStore(db)
    journal = ChangeJournal(db)
    sid = store.save(
        None,
        "demo",
        {"task": "add validation", "result": "done", "status": "success"},
        {"last_run_id": RUN_ID},
    )
    store.append_event(sid, RUN_ID, {"type": "agent_started", "agent": "planner"})
    store.append_event(sid, RUN_ID, {"type": "agent_started", "agent": "coder"})
    store.append_event(sid, RUN_ID, {"type": "agent_finished", "agent": "coder"})
    store.append_event(sid, RUN_ID, {"type": "test_finished", "ok": True, "exit_code": 0})
    store.append_event(sid, RUN_ID, {"type": "lint_finished", "ok": True, "exit_code": 0})

    target = tmp_path / "app.py"
    target.write_text("content")
    journal.record(RUN_ID, target, None, file_hash(target), True)

    store.record_usage(
        sid,
        RUN_ID,
        [{"model": "gpt-4o-mini", "provider": "openai", "prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500}],
    )
    return store, journal, sid


def _cli_settings(monkeypatch, tmp_path: Path) -> Settings:
    settings = Settings(session_db=tmp_path / "sessions.db")
    monkeypatch.setattr("devx.cli.app.Settings.load", staticmethod(lambda: settings))
    return settings


# ── build_report ────────────────────────────────────────────────────────────


def test_build_report_by_session_name(tmp_path: Path):
    store, journal, sid = _fixture(tmp_path)
    report = build_report(store, journal, "demo")
    assert report["session_id"] == sid
    assert report["run_id"] == RUN_ID
    assert report["goal"] == "add validation"
    assert report["result"] == "done"
    assert any(t["type"] == "agent_started" for t in report["timeline"])
    assert len(report["changes"]) == 1
    assert report["usage"]["calls"] == 1
    assert report["status"] == "success"


def test_build_report_by_run_id(tmp_path: Path):
    store, journal, _ = _fixture(tmp_path)
    report = build_report(store, journal, RUN_ID)
    assert report["run_id"] == RUN_ID
    assert report["goal"] == "add validation"


def test_build_report_unknown_raises_key_error(tmp_path: Path):
    store, journal, _ = _fixture(tmp_path)
    try:
        build_report(store, journal, "does-not-exist")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


def test_build_report_run_without_session(tmp_path: Path):
    db = tmp_path / "sessions.db"
    store = SessionStore(db)
    journal = ChangeJournal(db)
    run_key = "orphan-run"
    # Anonymous runs key their events and usage under the run id itself.
    store.append_event(run_key, run_key, {"type": "agent_started", "agent": "planner"})
    store.record_usage(run_key, run_key, [{"model": "gpt-4o-mini", "provider": "openai", "prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}])

    report = build_report(store, journal, run_key)
    assert report["run_id"] == run_key
    assert report["session_id"] == run_key
    assert report["usage"]["calls"] == 1


# ── render_report ───────────────────────────────────────────────────────────


def test_render_report_contains_key_sections(tmp_path: Path):
    store, journal, _ = _fixture(tmp_path)
    markdown = render_report(build_report(store, journal, "demo"))
    for section in (
        "## Goal", "## Result", "## Timeline", "## Files changed",
        "## Suggested PR description", "## Cost",
    ):
        assert section in markdown
    assert "app.py" in markdown
    assert "run-abc" in markdown


def test_render_report_includes_checks(tmp_path: Path):
    store, journal, _ = _fixture(tmp_path)
    markdown = render_report(build_report(store, journal, "demo"))
    assert "[ok] test: passed" in markdown
    assert "[ok] lint: passed" in markdown


def test_render_report_is_console_safe_ascii(tmp_path: Path):
    # Rendered markdown is echoed to the console (`devx report` without --out),
    # which on Windows legacy consoles/pipes encodes with cp1252 and would crash
    # on glyphs like ✓ (U+2713). Guard that the output is cp1252-encodable.
    store, journal, _ = _fixture(tmp_path)
    markdown = render_report(build_report(store, journal, "demo"))
    assert markdown.encode("cp1252")  # raises UnicodeEncodeError on regression


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_cli_report_json(tmp_path: Path, monkeypatch):
    _fixture(tmp_path)
    _cli_settings(monkeypatch, tmp_path)
    result = runner.invoke(app, ["report", "demo", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["goal"] == "add validation"


def test_cli_report_markdown_out(tmp_path: Path, monkeypatch):
    _fixture(tmp_path)
    _cli_settings(monkeypatch, tmp_path)
    out = tmp_path / "report.md"
    result = runner.invoke(app, ["report", "demo", "--out", str(out)])
    assert result.exit_code == 0
    assert out.is_file()
    assert "## Goal" in out.read_text(encoding="utf-8")


def test_cli_report_by_run(tmp_path: Path, monkeypatch):
    _fixture(tmp_path)
    _cli_settings(monkeypatch, tmp_path)
    result = runner.invoke(app, ["report", RUN_ID])
    assert result.exit_code == 0
    assert "## Goal" in result.output


def test_cli_report_unknown(tmp_path: Path, monkeypatch):
    _cli_settings(monkeypatch, tmp_path)
    result = runner.invoke(app, ["report", "nope"])
    assert result.exit_code == 2
    assert "No session or run found" in result.output


# ── usage receipts ──────────────────────────────────────────────────────────


def test_render_usage_report_markdown():
    report = {
        "calls": 2,
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "total_tokens": 1500,
        "estimated_cost_usd": 0.0007,
        "unpriced_models": [],
        "by_model": [{"provider": "openai", "model": "gpt-4o-mini", "calls": 2,
                      "prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500,
                      "estimated_cost_usd": 0.0007}],
    }
    markdown = render_usage_report(report)
    assert "# Token usage" in markdown
    assert "gpt-4o-mini" in markdown
    assert "| provider | model | calls |" in markdown


def test_cli_usage_run_markdown_receipt(tmp_path: Path, monkeypatch):
    db = tmp_path / "sessions.db"
    store = SessionStore(db)
    sid = store.save(None, "s", {"task": "t"})
    store.append_event(sid, "run-1", {"type": "agent_started", "agent": "planner"})
    store.record_usage(
        sid, "run-1",
        [{"model": "gpt-4o-mini", "provider": "openai", "prompt_tokens": 1_000_000, "completion_tokens": 0, "total_tokens": 1_000_000}],
    )
    _cli_settings(monkeypatch, tmp_path)

    result = runner.invoke(app, ["usage", "--run", "run-1", "--markdown"])
    assert result.exit_code == 0
    assert "# Usage receipt" in result.output
    assert "run-1" in result.output
    assert "gpt-4o-mini" in result.output


def test_cli_usage_run_filters_to_run(tmp_path: Path, monkeypatch):
    db = tmp_path / "sessions.db"
    store = SessionStore(db)
    store.record_usage("s1", "run-1", [{"model": "gpt-4o-mini", "provider": "openai", "prompt_tokens": 100, "completion_tokens": 0, "total_tokens": 100}])
    store.record_usage("s1", "run-2", [{"model": "gpt-4o-mini", "provider": "openai", "prompt_tokens": 900, "completion_tokens": 0, "total_tokens": 900}])
    _cli_settings(monkeypatch, tmp_path)

    result = runner.invoke(app, ["usage", "--run", "run-2", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["calls"] == 1
    assert payload["total_tokens"] == 900


def test_aggregate_usage_by_run_only(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions.db")
    store.record_usage(None, "run-a", [{"model": "gpt-4o-mini", "provider": "openai", "prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10}])
    store.record_usage(None, "run-b", [{"model": "gpt-4o-mini", "provider": "openai", "prompt_tokens": 90, "completion_tokens": 0, "total_tokens": 90}])
    report = store.aggregate_usage(run_id="run-b")
    assert report["calls"] == 1
    assert report["total_tokens"] == 90


def test_cli_report_rejects_both_remote_and_container(tmp_path: Path, monkeypatch):
    _cli_settings(monkeypatch, tmp_path)
    result = runner.invoke(app, ["report", "s1", "--remote", "ssh://user@host", "--container", "container://c1"])
    assert result.exit_code != 0
    assert "Cannot specify both --remote and --container" in result.output