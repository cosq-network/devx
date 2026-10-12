"""Tests for the team-layer prototype (``devx team``)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from devx.cli.app import app
from devx.harness.session import SessionStore
from devx.team import (
    TeamManifest,
    collect_team_metrics,
    create_team,
    load_team,
    save_team,
    team_config_path,
)

runner = CliRunner()


def test_create_requires_non_empty_name(tmp_path: Path):
    with pytest.raises(ValueError):
        TeamManifest.create("   ", workspace=tmp_path)


def test_create_and_load_round_trip(tmp_path: Path):
    manifest = create_team(tmp_path, "acme")
    assert manifest.name == "acme"
    assert manifest.team_id
    assert (tmp_path / ".devx" / "team.json").is_file()

    loaded = load_team(tmp_path)
    assert loaded is not None
    assert loaded.team_id == manifest.team_id
    assert loaded.name == "acme"
    assert loaded.shared_session_db == manifest.shared_session_db


def test_load_team_returns_none_when_absent(tmp_path: Path):
    assert load_team(tmp_path) is None
    assert not team_config_path(tmp_path).exists()


def test_load_team_rejects_corrupt_manifest(tmp_path: Path):
    path = team_config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_team(tmp_path)


def test_load_team_rejects_missing_fields(tmp_path: Path):
    path = team_config_path(tmp_path)
    path.parent.mkdir(parents=True)
    save_team(tmp_path, TeamManifest(team_id="t", name="n", shared_session_db="x"))
    path.write_text('{"name": "only-name"}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_team(tmp_path)


def test_add_member_deduplicates(tmp_path: Path):
    manifest = create_team(tmp_path, "acme")
    assert manifest.add_member("alice") is True
    assert manifest.add_member("alice") is False
    assert manifest.add_member("bob") is True
    assert manifest.members == ["alice", "bob"]
    with pytest.raises(ValueError):
        manifest.add_member("   ")


def test_shared_session_db_defaults_under_devx(tmp_path: Path):
    manifest = create_team(tmp_path, "acme")
    assert Path(manifest.shared_session_db) == (tmp_path / ".devx" / "sessions.db")


def test_collect_team_metrics_counts_sessions_and_events(tmp_path: Path):
    manifest = create_team(tmp_path, "acme")
    store = SessionStore(Path(manifest.shared_session_db))
    sid = store.save(None, "shared run", {"task": "x"})
    store.append_event(sid, "run-1", {"type": "done"})
    store.append_event(sid, "run-1", {"type": "done"})

    metrics = collect_team_metrics(manifest)
    assert metrics["team_name"] == "acme"
    assert metrics["sessions"] == 1
    assert metrics["events"] == 2
    assert metrics["dlq_entries"] == 0
    assert metrics["members"] == 0
    assert metrics["team_id"] == manifest.team_id


def test_collect_team_metrics_requires_shared_db():
    manifest = TeamManifest(team_id="t", name="n", shared_session_db="")
    with pytest.raises(ValueError):
        collect_team_metrics(manifest)


def test_cli_team_init_status_and_invite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["team", "init", "acme"])
    assert result.exit_code == 0, result.output
    assert "acme" in result.output

    result = runner.invoke(app, ["team", "invite", "alice"])
    assert result.exit_code == 0
    assert "alice" in result.output

    result = runner.invoke(app, ["team", "invite", "alice"])
    assert result.exit_code == 0
    assert "Already present" in result.output

    result = runner.invoke(app, ["team", "status"])
    assert result.exit_code == 0
    assert "team_name: acme" in result.output
    assert "members: 1" in result.output


def test_cli_team_list_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["team", "init", "acme"])

    manifest = load_team(tmp_path)
    assert manifest is not None
    store = SessionStore(Path(manifest.shared_session_db))
    store.save(None, "team-session", {"task": "x"})

    result = runner.invoke(app, ["team", "list-sessions"])
    assert result.exit_code == 0
    assert "team-session" in result.output


def test_cli_team_requires_init_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["team", "status"])
    assert result.exit_code != 0


def test_cli_team_rejects_unknown_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["team", "init", "acme"])
    result = runner.invoke(app, ["team", "bogus"])
    assert result.exit_code != 0
    assert "devx team" in result.output
