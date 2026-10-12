"""Drift-check tests: ``ChangeJournal.verify`` and the ``devx verify`` CLI."""

from pathlib import Path

from typer.testing import CliRunner

from devx.cli.app import app
from devx.config import Settings
from devx.harness.changes import ChangeJournal, file_hash

runner = CliRunner()


def test_verify_intact_conflict_missing(tmp_path: Path):
    journal = ChangeJournal(tmp_path / "changes.db")

    created = tmp_path / "created.py"
    modified = tmp_path / "modified.py"
    gone = tmp_path / "gone.py"

    created.write_text("v1")
    journal.record("r1", created, None, file_hash(created), True)

    modified.write_text("before")
    before = file_hash(modified)
    modified.write_text("after")
    journal.record("r1", modified, before, file_hash(modified), True)
    modified.write_text("hand-edited, matching neither recorded hash")

    gone.write_text("before-gone")
    before_gone = file_hash(gone)
    gone.write_text("after-gone")
    journal.record("r1", gone, before_gone, file_hash(gone), True)
    gone.unlink()

    by_path = {row["path"]: row["status"] for row in journal.verify("r1")}
    assert by_path[str(created)] == "intact"
    assert by_path[str(modified)] == "conflict"
    assert by_path[str(gone)] == "missing"


def test_verify_detects_reverted_modified_file(tmp_path: Path):
    journal = ChangeJournal(tmp_path / "changes.db")
    target = tmp_path / "a.py"
    target.write_text("original")
    before = file_hash(target)
    target.write_text("after")
    journal.record("r1", target, before, file_hash(target), True)
    target.write_text("original")  # restored
    rows = journal.verify("r1")
    assert rows[0]["status"] == "reverted"


def test_verify_detects_reverted_created_file(tmp_path: Path):
    journal = ChangeJournal(tmp_path / "changes.db")
    target = tmp_path / "new.py"
    target.write_text("new")
    journal.record("r1", target, None, file_hash(target), True)
    target.unlink()
    rows = journal.verify("r1")
    assert rows[0]["status"] == "reverted"


def test_verify_run_filter(tmp_path: Path):
    journal = ChangeJournal(tmp_path / "changes.db")
    one = tmp_path / "one.py"
    two = tmp_path / "two.py"
    one.write_text("1")
    two.write_text("2")
    journal.record("r1", one, None, file_hash(one), True)
    journal.record("r2", two, None, file_hash(two), True)
    rows = journal.verify("r2")
    assert len(rows) == 1
    assert rows[0]["path"].endswith("two.py")


def test_verify_empty_journal(tmp_path: Path):
    journal = ChangeJournal(tmp_path / "changes.db")
    assert journal.verify() == []


# ── CLI ─────────────────────────────────────────────────────────────────────


def _cli_settings(monkeypatch, tmp_path: Path) -> Settings:
    settings = Settings(session_db=tmp_path / "sessions.db")
    monkeypatch.setattr("devx.cli.app.Settings.load", staticmethod(lambda: settings))
    return settings


def test_cli_verify_reports_intact(tmp_path: Path, monkeypatch):
    _cli_settings(monkeypatch, tmp_path)
    journal = ChangeJournal(tmp_path / "sessions.db")
    target = tmp_path / "a.py"
    target.write_text("content")
    journal.record("r1", target, None, file_hash(target), True)

    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 0
    assert "intact" in result.output
    assert "a.py" in result.output


def test_cli_verify_exit_1_on_conflict(tmp_path: Path, monkeypatch):
    _cli_settings(monkeypatch, tmp_path)
    journal = ChangeJournal(tmp_path / "sessions.db")
    target = tmp_path / "a.py"
    target.write_text("after")
    journal.record("r1", target, None, file_hash(target), True)
    target.write_text("someone else edited this after the run")

    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 1
    assert "conflict" in result.output


def test_cli_verify_empty(tmp_path: Path, monkeypatch):
    _cli_settings(monkeypatch, tmp_path)
    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 0
    assert "No recorded changes" in result.output


def test_cli_verify_json(tmp_path: Path, monkeypatch):
    import json

    _cli_settings(monkeypatch, tmp_path)
    journal = ChangeJournal(tmp_path / "sessions.db")
    target = tmp_path / "a.py"
    target.write_text("content")
    journal.record("r1", target, None, file_hash(target), True)

    result = runner.invoke(app, ["verify", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload[0]["status"] == "intact"
    assert payload[0]["path"].endswith("a.py")