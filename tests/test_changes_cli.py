from pathlib import Path

from typer.testing import CliRunner

from devx.cli.app import app
from devx.config import Settings
from devx.harness.changes import ChangeJournal, file_hash

runner = CliRunner()


def _patched_settings(monkeypatch, db: Path) -> Settings:
    settings = Settings(session_db=db)
    monkeypatch.setattr("devx.cli.app.Settings.load", staticmethod(lambda: settings))
    return settings


# ── journal: rollback_one / runs / matching ────────────────────────────────


def test_rollback_one_removes_created_file(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("created")
    journal = ChangeJournal(tmp_path / "changes.db")
    journal.record("r1", target, None, file_hash(target), approved=True)

    assert journal.rollback_one("r1", "a.py") is True
    assert not target.exists()


def test_rollback_one_respects_hash_guard(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("created")
    journal = ChangeJournal(tmp_path / "changes.db")
    journal.record("r1", target, None, file_hash(target), approved=True)

    # Someone edits the file after the run: reject must not clobber it.
    target.write_text("manually edited")
    assert journal.rollback_one("r1", "a.py") is False
    assert target.read_text() == "manually edited"


def test_rollback_one_returns_false_for_unknown_path(tmp_path: Path):
    journal = ChangeJournal(tmp_path / "changes.db")
    target = tmp_path / "a.py"
    target.write_text("x")
    journal.record("r1", target, None, file_hash(target), approved=True)
    assert journal.rollback_one("r1", "other.py") is False
    assert target.exists()


def test_rollback_one_restores_backup(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("original")
    backup = tmp_path / "a.py.bak"
    backup.write_text("original")
    journal = ChangeJournal(tmp_path / "changes.db")
    journal.record("r1", target, "before-hash", file_hash(target), approved=True, backup_path=backup)

    target.write_text("modified-but-recorded")
    journal.record("r2", target, "mid-hash", file_hash(target), approved=True, backup_path=backup)
    assert journal.rollback_one("r2", "a.py") is True
    assert target.read_text() == "original"


def test_runs_are_newest_first(tmp_path: Path):
    journal = ChangeJournal(tmp_path / "changes.db")
    one = tmp_path / "one.py"
    one.write_text("1")
    two = tmp_path / "two.py"
    two.write_text("2")
    journal.record("first", one, None, file_hash(one), True)
    journal.record("second", two, None, file_hash(two), True)
    assert journal.runs()[0] == "second"


def test_matches_accepts_absolute_relative_and_basename():
    assert ChangeJournal._matches("/srv/app/a.py", "a.py")
    assert ChangeJournal._matches("/srv/app/a.py", "app/a.py")
    assert ChangeJournal._matches("/srv/app/a.py", "/srv/app/a.py")
    assert not ChangeJournal._matches("/srv/app/a.py", "b.py")


# ── CLI ────────────────────────────────────────────────────────────────────


def test_cli_changes_list(tmp_path: Path, monkeypatch):
    db = tmp_path / "sessions.db"
    journal = ChangeJournal(db)
    target = tmp_path / "src" / "a.py"
    target.parent.mkdir()
    target.write_text("x")
    journal.record("run-x", target, "before", "after", approved=True)
    _patched_settings(monkeypatch, db)

    result = runner.invoke(app, ["changes", "list"])
    assert result.exit_code == 0
    assert "run-x" in result.output
    assert "a.py" in result.output
    assert "approved" in result.output


def test_cli_changes_list_json(tmp_path: Path, monkeypatch):
    import json

    db = tmp_path / "sessions.db"
    journal = ChangeJournal(db)
    target = tmp_path / "a.py"
    target.write_text("x")
    journal.record("run-json", target, None, file_hash(target), approved=True)
    _patched_settings(monkeypatch, db)

    result = runner.invoke(app, ["changes", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload[0]["run_id"] == "run-json"


def test_cli_changes_accept_keeps_file(tmp_path: Path, monkeypatch):
    db = tmp_path / "sessions.db"
    journal = ChangeJournal(db)
    target = tmp_path / "a.py"
    target.write_text("created")
    journal.record("run-a", target, None, file_hash(target), approved=True)
    _patched_settings(monkeypatch, db)

    result = runner.invoke(app, ["changes", "accept", "a.py", "--run", "run-a"])
    assert result.exit_code == 0
    assert "Kept" in result.output
    assert target.exists()


def test_cli_changes_reject_reverts_file(tmp_path: Path, monkeypatch):
    db = tmp_path / "sessions.db"
    journal = ChangeJournal(db)
    target = tmp_path / "a.py"
    target.write_text("created")
    journal.record("run-r", target, None, file_hash(target), approved=True)
    _patched_settings(monkeypatch, db)

    result = runner.invoke(app, ["changes", "reject", "a.py", "--run", "run-r"])
    assert result.exit_code == 0
    assert "Reverted" in result.output
    assert not target.exists()


def test_cli_changes_reject_uses_latest_run_by_default(tmp_path: Path, monkeypatch):
    db = tmp_path / "sessions.db"
    journal = ChangeJournal(db)
    target = tmp_path / "a.py"
    target.write_text("created")
    journal.record("only-run", target, None, file_hash(target), approved=True)
    _patched_settings(monkeypatch, db)

    result = runner.invoke(app, ["changes", "reject", "a.py"])
    assert result.exit_code == 0
    assert not target.exists()


def test_cli_changes_reject_modified_file_fails(tmp_path: Path, monkeypatch):
    db = tmp_path / "sessions.db"
    journal = ChangeJournal(db)
    target = tmp_path / "a.py"
    target.write_text("created")
    journal.record("run-m", target, None, file_hash(target), approved=True)
    target.write_text("changed since run")
    _patched_settings(monkeypatch, db)

    result = runner.invoke(app, ["changes", "reject", "a.py", "--run", "run-m"])
    assert result.exit_code == 2
    assert target.exists()


def test_cli_changes_requires_path(tmp_path: Path, monkeypatch):
    _patched_settings(monkeypatch, tmp_path / "sessions.db")
    result = runner.invoke(app, ["changes", "reject"])
    assert result.exit_code == 2
