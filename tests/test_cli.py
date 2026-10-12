from typer.testing import CliRunner

from devx.cli.app import app


def test_cli_without_subcommand_starts_default_tui(monkeypatch):
    started = []

    class FakeTUI:
        def __init__(self, session_id=None):
            started.append(("init", session_id))

        def run(self):
            started.append(("run",))

    monkeypatch.setattr("devx.tui.app.DevTUI", FakeTUI)

    result = CliRunner().invoke(app, [])

    assert result.exit_code == 0, result.stdout
    assert started == [("init", None), ("run",)]


def test_explicit_tui_command_preserves_session_option(monkeypatch):
    started = []

    class FakeTUI:
        def __init__(self, session_id=None):
            started.append(("init", session_id))

        def run(self):
            started.append(("run",))

    monkeypatch.setattr("devx.tui.app.DevTUI", FakeTUI)

    result = CliRunner().invoke(app, ["tui", "--session", "local-dev"])

    assert result.exit_code == 0, result.stdout
    assert started == [("init", "local-dev"), ("run",)]


def test_cli_init_non_interactive_writes_config(tmp_path, monkeypatch):
    # Regression: `devx init` imported UserConfig/PROVIDERS/catalog_models from
    # devx.config (which only ships Settings) and crashed with ImportError; the
    # success banner also carried a ✓ (U+2713) that cp1252 consoles reject.
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["init", "--non-interactive", "-p", "ollama"])

    assert result.exit_code == 0, result.output
    assert "Configuration saved:" in result.output
    assert (tmp_path / ".devx" / "config.json").is_file()
    assert (tmp_path / ".env").is_file()
