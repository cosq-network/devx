"""Unit tests for the ``devx reset`` command and its filesystem helpers.

Regression coverage for the broken state shipped in the ``devx reset`` commit:

* the CLI file did not compile (unterminated f-string), so ``devx --help`` crashed;
* the reset helpers were duplicated three times in the module;
* ``_reset_cache`` referenced an undefined ``devx_global`` name;
* the Typer options were declared with ``--all`` / ``--global`` style Python
  parameter names, which is not valid Python;
* ``--all`` defaulted to ``True`` and was checked first, so ``--global``,
  ``--cache`` and friends were unreachable;
* the scoped helpers echoed "Reset cancelled." but continued to delete files.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from devx.cli import reset as reset_ops
from devx.cli.app import app


# ── helpers ───────────────────────────────────────────────────────────────────


def _seed_home(home: Path) -> None:
    """Create a populated ``~/.devx`` and ``~/.cache/devx`` tree."""
    (home / ".devx").mkdir(parents=True, exist_ok=True)
    (home / ".cache" / "devx").mkdir(parents=True, exist_ok=True)
    (home / ".devx" / "config.json").write_text("{}", encoding="utf-8")
    (home / ".devx" / "config.env").write_text("KEY=value\n", encoding="utf-8")
    (home / ".devx" / "sessions.db").write_text("sessions", encoding="utf-8")
    (home / ".devx" / "cache.db").write_text("cache", encoding="utf-8")
    (home / ".cache" / "devx" / "model.onnx").write_text("weights", encoding="utf-8")


def _seed_project(workspace: Path) -> None:
    (workspace / ".devx").mkdir(parents=True, exist_ok=True)
    (workspace / ".devx" / "config.json").write_text("{}", encoding="utf-8")
    (workspace / ".env").write_text("KEY=value\n", encoding="utf-8")


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate HOME and the working directory for reset scenarios."""
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(workspace)
    return home, workspace


# ── module level primitives ───────────────────────────────────────────────────


def test_reset_scopes_are_declared() -> None:
    assert reset_ops.RESET_SCOPES == ("all", "global", "project", "cache", "sessions")


def test_global_dir_points_at_devx_home() -> None:
    assert reset_ops.global_dir() == Path.home() / ".devx"


def test_global_config_paths_cover_config_env_and_sessions() -> None:
    names = {p.name for p in reset_ops.global_config_paths()}
    assert names == {"config.json", "config.env", "sessions.db"}


def test_cache_db_path_is_global() -> None:
    assert reset_ops.cache_db_path() == Path.home() / ".devx" / "cache.db"


def test_project_config_paths_point_into_workspace(tmp_path: Path) -> None:
    assert reset_ops.project_config_paths(tmp_path) == (
        tmp_path / ".devx",
        tmp_path / ".env",
    )


def test_reset_global_config_removes_files(sandbox) -> None:
    home, _ = sandbox
    _seed_home(home)

    removed = reset_ops.reset_global_config()

    assert {p.name for p in removed} == {"config.json", "config.env", "sessions.db"}
    assert not (home / ".devx" / "config.json").exists()
    assert not (home / ".devx" / "config.env").exists()
    assert not (home / ".devx" / "sessions.db").exists()
    # cache.db is *not* part of the global config scope
    assert (home / ".devx" / "cache.db").exists()


def test_reset_sessions_keeps_config(sandbox) -> None:
    home, _ = sandbox
    _seed_home(home)

    removed = reset_ops.reset_sessions()

    assert [p.name for p in removed] == ["sessions.db"]
    assert not (home / ".devx" / "sessions.db").exists()
    assert (home / ".devx" / "config.json").exists()
    assert (home / ".devx" / "config.env").exists()


def test_reset_cache_removes_cache_dir_and_db(sandbox) -> None:
    home, _ = sandbox
    _seed_home(home)

    removed = reset_ops.reset_cache()

    assert len(removed) == 2
    assert not (home / ".cache" / "devx").exists()
    assert not (home / ".devx" / "cache.db").exists()
    # unrelated global files survive
    assert (home / ".devx" / "config.json").exists()


def test_reset_project_config_removes_devx_dir_and_env(sandbox) -> None:
    home, workspace = sandbox
    _seed_home(home)
    _seed_project(workspace)

    removed = reset_ops.reset_project_config(workspace)

    assert len(removed) == 2
    assert not (workspace / ".devx").exists()
    assert not (workspace / ".env").exists()
    assert (home / ".devx" / "config.json").exists()


def test_reset_helpers_are_idempotent(sandbox) -> None:
    home, workspace = sandbox
    _seed_home(home)
    _seed_project(workspace)

    reset_ops.reset_global_config()
    reset_ops.reset_sessions()
    reset_ops.reset_cache()
    reset_ops.reset_project_config(workspace)

    assert reset_ops.reset_global_config() == []
    assert reset_ops.reset_sessions() == []
    assert reset_ops.reset_cache() == []
    assert reset_ops.reset_project_config(workspace) == []


def test_reset_project_config_ignores_missing_paths(sandbox) -> None:
    _, workspace = sandbox
    assert reset_ops.reset_project_config(workspace) == []


# ── CLI behaviour ─────────────────────────────────────────────────────────────


def test_reset_help_renders(sandbox) -> None:
    result = CliRunner().invoke(app, ["reset", "--help"])

    assert result.exit_code == 0, result.output
    for flag in ("--all", "--global", "--project", "--cache", "--sessions", "--force", "--yes"):
        assert flag in result.output


def test_top_level_help_renders(sandbox) -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    assert "reset" in result.output


def test_reset_default_scope_is_everything(sandbox) -> None:
    home, workspace = sandbox
    _seed_home(home)
    _seed_project(workspace)

    result = CliRunner().invoke(app, ["reset", "--yes"])

    assert result.exit_code == 0, result.output
    assert not (home / ".devx" / "config.json").exists()
    assert not (home / ".devx" / "config.env").exists()
    assert not (home / ".devx" / "sessions.db").exists()
    assert not (home / ".devx" / "cache.db").exists()
    assert not (home / ".cache" / "devx").exists()
    assert not (workspace / ".devx").exists()
    assert not (workspace / ".env").exists()
    assert "Reset complete!" in result.output


def test_reset_global_scope(sandbox) -> None:
    home, workspace = sandbox
    _seed_home(home)
    _seed_project(workspace)

    result = CliRunner().invoke(app, ["reset", "--global", "--yes"])

    assert result.exit_code == 0, result.output
    assert not (home / ".devx" / "config.json").exists()
    assert not (home / ".devx" / "config.env").exists()
    assert not (home / ".devx" / "sessions.db").exists()
    assert (home / ".devx" / "cache.db").exists()
    assert (workspace / ".devx").exists()


def test_reset_project_scope(sandbox) -> None:
    home, workspace = sandbox
    _seed_home(home)
    _seed_project(workspace)

    result = CliRunner().invoke(app, ["reset", "--project", "--yes"])

    assert result.exit_code == 0, result.output
    assert not (workspace / ".devx").exists()
    assert not (workspace / ".env").exists()
    assert (home / ".devx" / "config.json").exists()


def test_reset_cache_scope(sandbox) -> None:
    home, workspace = sandbox
    _seed_home(home)
    _seed_project(workspace)

    result = CliRunner().invoke(app, ["reset", "--cache", "--yes"])

    assert result.exit_code == 0, result.output
    assert not (home / ".cache" / "devx").exists()
    assert not (home / ".devx" / "cache.db").exists()
    assert (home / ".devx" / "config.json").exists()


def test_reset_sessions_scope(sandbox) -> None:
    home, workspace = sandbox
    _seed_home(home)
    _seed_project(workspace)

    result = CliRunner().invoke(app, ["reset", "--sessions", "--yes"])

    assert result.exit_code == 0, result.output
    assert not (home / ".devx" / "sessions.db").exists()
    assert (home / ".devx" / "config.json").exists()
    assert (home / ".devx" / "config.env").exists()


def test_reset_force_skips_confirmation(sandbox) -> None:
    home, _ = sandbox
    _seed_home(home)

    result = CliRunner().invoke(app, ["reset", "--global", "--force"])

    assert result.exit_code == 0, result.output
    assert not (home / ".devx" / "config.json").exists()


def test_reset_rejects_multiple_scopes(sandbox) -> None:
    home, _ = sandbox
    _seed_home(home)

    result = CliRunner().invoke(app, ["reset", "--global", "--cache", "--yes"])

    assert result.exit_code != 0
    assert "only one reset scope" in result.output
    # nothing was deleted
    assert (home / ".devx" / "config.json").exists()


def test_reset_without_confirmation_flag_in_non_tty_does_nothing(sandbox) -> None:
    home, _ = sandbox
    _seed_home(home)

    result = CliRunner().invoke(app, ["reset", "--global"])

    assert result.exit_code == 0, result.output
    assert "Reset cancelled." in result.output
    assert (home / ".devx" / "config.json").exists()


def test_reset_declined_prompt_keeps_files(sandbox) -> None:
    home, _ = sandbox
    _seed_home(home)

    result = CliRunner().invoke(app, ["reset", "--global"], input="n\n")

    assert result.exit_code == 0, result.output
    assert "Reset cancelled." in result.output
    assert (home / ".devx" / "config.json").exists()


def test_reset_accepted_prompt_deletes_files(sandbox) -> None:
    home, _ = sandbox
    _seed_home(home)

    result = CliRunner().invoke(app, ["reset", "--global"], input="y\n")

    assert result.exit_code == 0, result.output
    assert not (home / ".devx" / "config.json").exists()


def test_reset_on_clean_install_reports_nothing_to_remove(sandbox) -> None:
    result = CliRunner().invoke(app, ["reset", "--yes"])

    assert result.exit_code == 0, result.output
    assert "Nothing to remove" in result.output
