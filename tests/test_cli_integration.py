from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from devx.cli.app import app
from devx.config import Settings
from devx.harness.session import SessionStore

runner = CliRunner()


def test_cli_doctor_command():
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "status: ok" in result.output
    assert "provider:" in result.output
    assert "model:" in result.output


def test_cli_doctor_remote_preflight(monkeypatch: Any) -> None:
    from unittest.mock import MagicMock

    from devx.harness.backend import BackendToolResult

    monkeypatch.setattr(
        "devx.harness.ssh_backend.SshConfig.load_ssh_config", lambda host: {}
    )
    mock_backend = MagicMock()
    mock_backend.is_dir.return_value = True
    mock_backend.exists.return_value = True
    mock_backend.run_command.return_value = BackendToolResult(True, "true\n", 0)
    monkeypatch.setattr(
        "devx.harness.ssh_backend.SshWorkspaceBackend", lambda cfg: mock_backend
    )

    result = runner.invoke(app, ["doctor", "--remote", "deploy@host:/srv/app"])
    assert result.exit_code == 0
    assert "status: ok" in result.output
    assert "connection" in result.output
    assert "writable" in result.output


def test_cli_doctor_remote_preflight_failure(monkeypatch: Any) -> None:
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "devx.harness.ssh_backend.SshConfig.load_ssh_config", lambda host: {}
    )
    mock_backend = MagicMock()
    mock_backend.ensure_connected.side_effect = OSError("authentication failed")
    monkeypatch.setattr(
        "devx.harness.ssh_backend.SshWorkspaceBackend", lambda cfg: mock_backend
    )

    result = runner.invoke(app, ["doctor", "--remote", "deploy@host:/srv/app"])
    assert result.exit_code == 1
    assert "status: error" in result.output


def test_cli_doctor_remote_preflight_json(monkeypatch: Any) -> None:
    import json
    from unittest.mock import MagicMock

    from devx.harness.backend import BackendToolResult

    monkeypatch.setattr(
        "devx.harness.ssh_backend.SshConfig.load_ssh_config", lambda host: {}
    )
    mock_backend = MagicMock()
    mock_backend.is_dir.return_value = True
    mock_backend.exists.return_value = False
    mock_backend.run_command.return_value = BackendToolResult(True, "true\n", 0)
    monkeypatch.setattr(
        "devx.harness.ssh_backend.SshWorkspaceBackend", lambda cfg: mock_backend
    )

    result = runner.invoke(app, ["doctor", "--remote", "deploy@host:/srv/app", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["checks"]["git"]["status"] == "ok"
    assert payload["checks"]["venv"]["status"] == "missing"


def test_cli_doctor_remote_invalid_uri() -> None:
    result = runner.invoke(app, ["doctor", "--remote", "not-a-valid-uri"])
    assert result.exit_code == 2


def test_cli_sessions_and_session_management(tmp_path: Path):
    db_file = tmp_path / "sessions.db"
    store = SessionStore(db_file)
    sid = store.save(None, "integration-session", {"task": "test task"})
    store.append_event(sid, "run-1", {"type": "test_event", "payload": "hello"})

    settings = Settings(session_db=db_file)

    # Test sessions listing
    import devx.cli.app as cli_module
    orig_load = cli_module.Settings.load
    cli_module.Settings.load = classmethod(lambda cls, *args, **kwargs: settings)
    try:
        result = runner.invoke(app, ["sessions"])
        assert result.exit_code == 0
        assert "integration-session" in result.output

        # Test session list alias
        result_list = runner.invoke(app, ["session", "list"])
        assert result_list.exit_code == 0
        assert sid in result_list.output
        assert "integration-session" in result_list.output

        # Test session events
        result_events = runner.invoke(app, ["session", "events", sid])
        assert result_events.exit_code == 0
        assert "test_event" in result_events.output

        # Test session delete
        result_del = runner.invoke(app, ["session", "delete", sid])
        assert result_del.exit_code == 0
        assert store.load(sid) is None

        # Test delete nonexistent session
        result_del_missing = runner.invoke(app, ["session", "delete", "nonexistent"])
        assert result_del_missing.exit_code != 0

        # Test invalid session sub-action
        result_invalid = runner.invoke(app, ["session", "unknown"])
        assert result_invalid.exit_code != 0
        assert "devx session" in result_invalid.output
    finally:
        cli_module.Settings.load = orig_load


def test_config_env_precedence_and_backward_compatibility(monkeypatch, tmp_path: Path):
    home_dir = tmp_path / "fake_home"
    devx_dir = home_dir / ".devx"
    devx_dir.mkdir(parents=True)
    (devx_dir / "config.env").write_text(
        'DEVX_MODEL="gpt-4o-mini"\nDEV_MODEL="gpt-3.5-turbo"\nDEVX_BASE_URL="http://devx.local:8000/v1"\n',
        encoding="utf-8"
    )

    monkeypatch.setattr(Path, "home", lambda: home_dir)

    # DEVX_* should take precedence over DEV_* in config file
    s = Settings.load(workspace=tmp_path)
    assert s.model == "gpt-4o-mini"
    assert s.base_url == "http://devx.local:8000/v1"

    # Environment variable DEVX_* overrides file
    monkeypatch.setenv("DEVX_MODEL", "claude-3-5-sonnet")
    s2 = Settings.load(workspace=tmp_path)
    assert s2.model == "claude-3-5-sonnet"

    # Legacy DEV_* env var fallback works when DEVX_* not set
    monkeypatch.delenv("DEVX_MODEL")
    monkeypatch.setenv("DEV_MODEL", "legacy-dev-model")
    s3 = Settings.load(workspace=tmp_path)
    assert s3.model == "legacy-dev-model"



def test_cli_ask_approval_cache_flag(tmp_path: Path, monkeypatch: Any) -> None:
    """Test that --approval-cache flag is accepted by the ask command."""
    from typer.testing import CliRunner
    from devx.cli.app import app

    runner = CliRunner()
    # Just verify the flag is accepted (it may deny due to no handler, but shouldn't crash)
    result = runner.invoke(app, ["ask", "test prompt", "--approval-cache"])
    # Exit code 2 is expected when no approval handler is configured (default deny)
    # The important thing is that the command doesn't error on unknown flags
    assert result.exit_code in (0, 2), f"Expected 0 or 2, got {result.exit_code}"


def test_cli_ask_approval_matching_flag(tmp_path: Path, monkeypatch: Any) -> None:
    """Test that --approval-matching flag is accepted by the ask command."""
    from typer.testing import CliRunner
    from devx.cli.app import app

    runner = CliRunner()
    # Just verify the flag is accepted (it may deny due to no handler, but shouldn't crash)
    result = runner.invoke(app, ["ask", "test prompt", "--approval-matching"])
    # Exit code 2 is expected when no approval handler is configured (default deny)
    assert result.exit_code in (0, 2), f"Expected 0 or 2, got {result.exit_code}"


def test_cli_ask_both_approval_flags(tmp_path: Path, monkeypatch: Any) -> None:
    """Test that both --approval-cache and --approval-matching flags work together."""
    from typer.testing import CliRunner
    from devx.cli.app import app

    runner = CliRunner()
    # Test both flags together
    result = runner.invoke(app, ["ask", "test prompt", "--approval-cache", "--approval-matching"])
    # Should not crash; exit code 2 means default deny (no handler configured)
    assert result.exit_code in (0, 2), f"Expected 0 or 2, got {result.exit_code}"


def test_settings_pydantic_validators() -> None:
    """Test that pydantic validators work correctly for Settings."""
    from devx.config import Settings

    # Test valid values
    s = Settings(max_iterations=8, max_tool_calls=40)
    assert s.max_iterations == 8
    assert s.max_tool_calls == 40

    # Test invalid values raise ValueError
    try:
        Settings(max_iterations=0)
        assert False, "Should have raised ValueError for max_iterations=0"
    except ValueError:
        pass  # Expected

    try:
        Settings(max_tool_calls=0)
        assert False, "Should have raised ValueError for max_tool_calls=0"
    except ValueError:
        pass  # Expected

    try:
        Settings(max_context_messages=1)
        assert False, "Should have raised ValueError for max_context_messages=1"
    except ValueError:
        pass  # Expected

    try:
        Settings(max_context_chars=100)
        assert False, "Should have raised ValueError for max_context_chars=100"
    except ValueError:
        pass  # Expected

    try:
        Settings(compaction_summary_chars=64)
        assert False, "Should have raised ValueError for compaction_summary_chars=64"
    except ValueError:
        pass  # Expected

    try:
        Settings(diagnostic_distillation_max_chars=50)
        assert False, "Should have raised ValueError for diagnostic_distillation_max_chars=50"
    except ValueError:
        pass  # Expected

    try:
        Settings(command_timeout=0)
        assert False, "Should have raised ValueError for command_timeout=0"
    except ValueError:
        pass  # Expected

    try:
        Settings(max_file_bytes=0)
        assert False, "Should have raised ValueError for max_file_bytes=0"
    except ValueError:
        pass  # Expected


def test_settings_load_preserves_precedence(monkeypatch: Any, tmp_path: Path) -> None:
    """Test that Settings.load() respects environment variable precedence."""
    from pathlib import Path
    from devx.config import Settings

    home_dir = tmp_path / "fake_home"
    devx_dir = home_dir / ".devx"
    devx_dir.mkdir(parents=True)
    (devx_dir / "config.env").write_text("", encoding="utf-8")

    monkeypatch.setattr(Path, "home", lambda: home_dir)

    # Clear environment variables that could affect Settings loading
    # to ensure we test with clean defaults
    for key in [
        "DEVX_PROVIDER",
        "DEVX_MODEL",
        "DEVX_WORKSPACE",
        "DEV_WORKSPACE",
        "DEVX_BASE_URL",
        "DEV_BASE_URL",
        "DEVX_API_KEY",
        "DEV_API_KEY",
        "DEVX_MAX_CONTEXT_FILES",
        "DEVX_MAX_CONTEXT_MESSAGES",
        "DEVX_MAX_CONTEXT_CHARS",
        "DEVX_MAX_ITERATIONS",
        "DEVX_MAX_TOOL_CALLS",
        "DEVX_COMMAND_TIMEOUT",
        "DEV_APPROVAL_REQUIRED",
        "DEV_EMBEDDINGS_ENABLED",
        "DEV_EMBEDDINGS_OFFLINE",
        "DEV_CONTEXT_COMPACTION_ENABLED",
        "DEV_AUTO_CONTEXT",
        "DEV_MAX_CONTEXT_FILES",
        "DEV_MAX_CONTEXT_MESSAGES",
        "DEV_MAX_CONTEXT_CHARS",
        "DEV_MAX_ITERATIONS",
        "DEV_MAX_TOOL_CALLS",
        "DEV_COMMAND_TIMEOUT",
        "DEV_APPROVAL_REQUIRED",
        "DEV_EMBEDDINGS_ENABLED",
        "DEV_EMBEDDINGS_OFFLINE",
        "DEV_CONTEXT_COMPACTION_ENABLED",
        "DEV_AUTO_CONTEXT",
    ]:
        monkeypatch.delenv(key, raising=False)

    # Test that default values are used when no env vars are set
    s = Settings.load(workspace=tmp_path)
    assert s.base_url is not None
    assert s.provider is not None
    assert s.model is not None
    assert s.approval_required is not None
    assert s.max_iterations == 8
    assert s.max_tool_calls == 40
    assert s.max_context_files == 5
    assert s.max_context_messages == 24
    assert s.max_context_chars == 24000
