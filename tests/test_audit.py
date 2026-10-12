import json
from pathlib import Path

from typer.testing import CliRunner

from devx.audit import collect_audit, export_audit, render_audit
from devx.cli.app import app
from devx.config import Settings
from devx.harness.changes import ChangeJournal
from devx.harness.session import SessionStore

runner = CliRunner()


def _seed(db_file: Path) -> tuple[SessionStore, ChangeJournal, str]:
    store = SessionStore(db_file)
    journal = ChangeJournal(db_file)
    sid = store.save(None, "audited-session", {"task": "t"})
    store.append_event(sid, "run-1", {"type": "tool_call", "name": "write"})
    store.add_to_dlq(sid, "run-1", "boom", {"step": 1}, {"severity": "low"})
    store.record_usage(
        sid, "run-1",
        [{"model": "gpt-4o-mini", "provider": "openai", "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}],
    )
    journal.record(
        run_id="run-1",
        path=Path("/srv/app/main.py"),
        before_hash="before",
        after_hash="after",
        approved=True,
    )
    return store, journal, sid


def test_collect_audit_includes_all_record_types(tmp_path: Path):
    store, journal, sid = _seed(tmp_path / "sessions.db")
    records = collect_audit(store, journal)
    types = {record["type"] for record in records}
    assert {"session", "event", "change", "dlq", "usage"} <= types


def test_collect_audit_scopes_to_session_and_run(tmp_path: Path):
    store, journal, sid = _seed(tmp_path / "sessions.db")
    store.save(None, "other", {"task": "other"})

    scoped = collect_audit(store, journal, session_id=sid, run_id="run-1")
    session_ids = {r["session_id"] for r in scoped if r["type"] == "event"}
    assert session_ids == {sid}
    change_runs = {r["run_id"] for r in scoped if r["type"] == "change"}
    assert change_runs == {"run-1"}


def test_render_audit_jsonl_and_json(tmp_path: Path):
    store, journal, sid = _seed(tmp_path / "sessions.db")
    records = collect_audit(store, journal)
    jsonl = render_audit(records, fmt="jsonl")
    assert all(json.loads(line)["type"] for line in jsonl.splitlines())
    pretty = render_audit(records, fmt="json")
    assert isinstance(json.loads(pretty), list)


def test_export_audit_writes_file(tmp_path: Path):
    store, journal, sid = _seed(tmp_path / "sessions.db")
    destination, count = export_audit(store, journal, tmp_path / "out" / "audit.jsonl")
    assert destination.is_file()
    assert count >= 5
    lines = destination.read_text(encoding="utf-8").strip().splitlines()
    assert all(json.loads(line)["type"] for line in lines)


def test_cli_audit_export_stdout(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "sessions.db"
    _seed(db_file)
    settings = Settings(session_db=db_file)
    monkeypatch.setattr("devx.cli.app.Settings.load", staticmethod(lambda: settings))

    result = runner.invoke(app, ["audit", "export"])
    assert result.exit_code == 0
    lines = [line for line in result.output.strip().splitlines() if line.strip()]
    record_types = {json.loads(line)["type"] for line in lines}
    assert {"session", "change", "dlq", "usage"} <= record_types


def test_cli_audit_export_to_file(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "sessions.db"
    _seed(db_file)
    settings = Settings(session_db=db_file)
    monkeypatch.setattr("devx.cli.app.Settings.load", staticmethod(lambda: settings))
    out_file = tmp_path / "audit.jsonl"

    result = runner.invoke(app, ["audit", "export", "--out", str(out_file)])
    assert result.exit_code == 0
    assert "Wrote" in result.output
    assert out_file.is_file()
    assert json.loads(out_file.read_text(encoding="utf-8").splitlines()[0])["type"]


def test_cli_audit_export_json_format(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "sessions.db"
    _seed(db_file)
    settings = Settings(session_db=db_file)
    monkeypatch.setattr("devx.cli.app.Settings.load", staticmethod(lambda: settings))

    result = runner.invoke(app, ["audit", "export", "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert any(record["type"] == "change" for record in payload)


def test_cli_audit_invalid_action_and_format(tmp_path: Path, monkeypatch):
    settings = Settings(session_db=tmp_path / "sessions.db")
    monkeypatch.setattr("devx.cli.app.Settings.load", staticmethod(lambda: settings))

    bad_action = runner.invoke(app, ["audit", "purge"])
    assert bad_action.exit_code == 2
    bad_format = runner.invoke(app, ["audit", "export", "--format", "xml"])
    assert bad_format.exit_code == 2
