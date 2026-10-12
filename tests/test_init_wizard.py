"""devx init TUI wizard and shared write-path tests.

Covers the ``apply_init`` helper (shared by the CLI command and the wizard),
the wizard's validation/prefill functions, and the CLI dispatch that routes a
plain interactive ``devx init`` to the TUI. No Textual app is launched: all
logic is exercised without a terminal.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devx.cli.app import app
from devx.configuration import ConfigurationError, apply_init
from devx.tui.init_wizard import project_prefill, validate_init

runner = CliRunner()


# ── apply_init (shared write path) ────────────────────────────────────────


class TestApplyInit:
    def test_writes_project_and_user_config(self, tmp_path: Path):
        result = apply_init(
            tmp_path,
            provider="openai",
            model="gpt-4o-mini",
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            embeddings=True,
            test_cmd="pytest tests/",
            lint_cmd="ruff check",
        )

        project = json.loads((tmp_path / ".devx" / "config.json").read_text(encoding="utf-8"))
        assert project["test_command"] == "pytest tests/"
        assert project["lint_command"] == "ruff check"
        assert project["context_compaction"] == {
            "enabled": True,
            "max_messages": 24,
            "max_chars": 24000,
            "summary_chars": 4000,
        }

        env = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "DEVX_PROVIDER=openai" in env
        assert "DEVX_MODEL=gpt-4o-mini" in env
        assert "DEVX_BASE_URL=https://api.openai.com/v1" in env
        assert "DEVX_EMBEDDINGS_ENABLED=true" in env
        assert "DEVX_API_KEY=sk-test" in env

        assert result["provider"] == "openai"
        assert result["model"] == "gpt-4o-mini"
        assert result["base_url"] == "https://api.openai.com/v1"
        assert result["embeddings"] is True
        assert result["config_path"] == tmp_path / ".devx" / "config.json"
        assert result["env_path"] == tmp_path / ".env"

    def test_merges_existing_project_config(self, tmp_path: Path):
        devx_dir = tmp_path / ".devx"
        devx_dir.mkdir()
        (devx_dir / "config.json").write_text(json.dumps({"policy_preset": "strict"}), encoding="utf-8")

        apply_init(tmp_path, provider="ollama", model="qwen2.5-coder:7b", api_key="ollama")

        project = json.loads((devx_dir / "config.json").read_text(encoding="utf-8"))
        assert project["policy_preset"] == "strict"
        assert project["test_command"] == ""
        assert project["lint_command"] == ""

    def test_embeddings_false_and_blank_prompt_fields(self, tmp_path: Path):
        apply_init(tmp_path, provider="ollama", model="qwen2.5-coder:7b", api_key="ollama")
        env = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "DEVX_EMBEDDINGS_ENABLED=false" in env
        assert "DEVX_BASE_URL=http://localhost:11434/v1" in env

    def test_missing_api_key_is_permissive_for_scripted_onboarding(self, tmp_path: Path):
        # Mirrors `devx init --non-interactive -p openai` (no key): succeeds,
        # writes config, and leaves DEVX_API_KEY out.
        apply_init(tmp_path, provider="openai", model="gpt-4o")
        env = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "DEVX_PROVIDER=openai" in env
        assert "DEVX_API_KEY=" not in env

    def test_unknown_provider_raises(self, tmp_path: Path):
        with pytest.raises(ConfigurationError):
            apply_init(tmp_path, provider="nope", model="gpt-4o")
        assert not (tmp_path / ".env").exists()

    def test_empty_model_raises(self, tmp_path: Path):
        with pytest.raises(ConfigurationError):
            apply_init(tmp_path, provider="openai", model="  ")


# ── wizard validation / prefill ───────────────────────────────────────────


class TestValidateInit:
    def test_blank_provider(self):
        assert validate_init("", "gpt-4o", "sk") == "Select a provider."

    def test_unknown_provider(self):
        assert validate_init("nope", "gpt-4o", "sk") == "Unknown provider: nope"

    def test_non_ollama_requires_api_key(self):
        assert validate_init("openai", "gpt-4o", "") == (
            "Enter an API key, or choose Ollama for a local model."
        )

    def test_provider_without_default_base_url_requires_one(self):
        assert validate_init("google", "gemini-2.0-flash", "sk", "") == (
            "google requires a base URL."
        )
        assert validate_init("google", "gemini-2.0-flash", "sk", "https://llm.example.com") is None

    def test_model_required(self):
        assert validate_init("openai", "", "sk", "https://api.openai.com/v1") == (
            "Select a coding model."
        )

    def test_valid_ollama_without_key(self):
        assert validate_init("ollama", "qwen2.5-coder:7b", "",
                             "http://localhost:11434/v1") is None


class TestProjectPrefill:
    def test_no_config(self, tmp_path: Path):
        prefill = project_prefill(tmp_path)
        assert prefill == {"test_command": "", "lint_command": "", "project": {}}

    def test_existing_config(self, tmp_path: Path):
        devx_dir = tmp_path / ".devx"
        devx_dir.mkdir()
        (devx_dir / "config.json").write_text(
            json.dumps({"test_command": "pytest", "policy_preset": "strict"}), encoding="utf-8"
        )
        prefill = project_prefill(tmp_path)
        assert prefill["test_command"] == "pytest"
        assert prefill["lint_command"] == ""
        assert prefill["project"] == {"test_command": "pytest", "policy_preset": "strict"}

    def test_invalid_config(self, tmp_path: Path):
        devx_dir = tmp_path / ".devx"
        devx_dir.mkdir()
        (devx_dir / "config.json").write_text("not json {", encoding="utf-8")
        assert project_prefill(tmp_path) == {"test_command": "", "lint_command": "", "project": {}}


# ── wizard UI (headless Textual pilot) ────────────────────────────────────


class TestInitWizardUi:
    async def _drive(self, actions, tmp_path: Path):
        from devx.tui.init_wizard import InitWizard

        wizard = InitWizard(workspace=tmp_path)
        async with wizard.run_test() as pilot:
            await actions(wizard, pilot)
            await pilot.pause()
        return wizard

    def test_save_writes_config_and_refreshes_on_provider_change(self, tmp_path: Path):
        import asyncio

        from textual.widgets import Button, Input, Select

        async def actions(wizard, pilot):
            provider = wizard.query_one("#wizard-provider", Select)
            base_url = wizard.query_one("#wizard-base-url", Input)
            model = wizard.query_one("#wizard-model", Select)

            # Defaults to Ollama (no key required), base URL prefilled.
            assert provider.value == "ollama"
            assert base_url.value == "http://localhost:11434/v1"
            assert str(model.value) == "qwen2.5-coder:7b"

            # Switching provider refreshes base URL and the curated model list.
            provider.value = "openai"
            await pilot.pause()
            assert base_url.value == "https://api.openai.com/v1"
            assert str(model.value) == "gpt-4.1"

            wizard.query_one("#wizard-api-key", Input).value = "sk-test-123"
            wizard.query_one("#wizard-save", Button).press()

        wizard = asyncio.run(self._drive(actions, tmp_path))
        assert wizard.result is not None
        assert wizard.result["provider"] == "openai"
        assert not wizard.cancelled

        env = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "DEVX_PROVIDER=openai" in env
        assert "DEVX_MODEL=gpt-4.1" in env
        assert "DEVX_API_KEY=sk-test-123" in env
        project = json.loads((tmp_path / ".devx" / "config.json").read_text(encoding="utf-8"))
        assert project["context_compaction"]["enabled"] is True

    def test_save_rejects_missing_api_key_inline(self, tmp_path: Path):
        import asyncio

        from textual.widgets import Button, Select

        async def actions(wizard, pilot):
            wizard.query_one("#wizard-provider", Select).value = "openai"
            await pilot.pause()
            # No API key: Save must show an inline error and write nothing.
            wizard.query_one("#wizard-save", Button).press()

        wizard = asyncio.run(self._drive(actions, tmp_path))
        assert wizard.result is None
        assert not wizard.cancelled
        assert not (tmp_path / ".env").exists()
        assert not (tmp_path / ".devx" / "config.json").exists()

    def test_cancel_writes_nothing(self, tmp_path: Path):
        import asyncio

        from textual.widgets import Button

        async def actions(wizard, pilot):
            wizard.query_one("#wizard-cancel", Button).press()

        wizard = asyncio.run(self._drive(actions, tmp_path))
        assert wizard.cancelled
        assert wizard.result is None
        assert not (tmp_path / ".env").exists()


# ── CLI dispatch ───────────────────────────────────────────────────────────


class TestInitWizardDispatch:
    def test_plain_interactive_init_launches_wizard(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        launched: list[Path] = []

        def fake_launch(workspace):
            launched.append(workspace)
            return True

        monkeypatch.setattr("devx.cli.app._is_interactive_tty", lambda: True)
        monkeypatch.setattr("devx.cli.app._launch_init_wizard", fake_launch)

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0, result.output
        assert launched == [tmp_path.resolve()]
        # The wizard owns configuration; the prompt path must not have run.
        assert not (tmp_path / ".env").exists()

    def test_flags_skip_wizard(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        launched = []
        monkeypatch.setattr("devx.cli.app._is_interactive_tty", lambda: True)
        monkeypatch.setattr(
            "devx.cli.app._launch_init_wizard",
            lambda workspace: launched.append(workspace) or True,
        )

        result = runner.invoke(app, ["init", "--non-interactive", "-p", "ollama"])

        assert result.exit_code == 0, result.output
        assert launched == []
        assert (tmp_path / ".env").is_file()
        assert "Configuration saved:" in result.output
        assert "Provider: ollama" in result.output

    def test_non_tty_never_launches_wizard(self, monkeypatch, tmp_path: Path):
        # Under CliRunner stdin/stdout are pipes, so isatty() is False and the
        # wizard must not be launched even with no flags.
        monkeypatch.chdir(tmp_path)
        launched = []
        monkeypatch.setattr(
            "devx.cli.app._launch_init_wizard",
            lambda workspace: launched.append(workspace) or True,
        )

        runner.invoke(app, ["init"])

        assert launched == []