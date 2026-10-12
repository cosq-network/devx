import asyncio
import json
from pathlib import Path

from devx import configuration
from devx.config import Settings
from devx.harness.session import SessionStore
from devx.tui.app import DevTUI
from textual.widgets import Static


async def submit_command(pilot, command: str) -> None:
    await pilot.click("#input-bar")
    await pilot.press(*list(command), "enter")
    await pilot.pause()


def test_live_model_discovery_flows_through_tui_to_model_selection(monkeypatch, tmp_path: Path):
    settings = Settings(
        provider="openrouter",
        api_key="test-key",
        base_url="https://openrouter.ai/api/v1",
        model="poolside/laguna-s-2.1:free",
        workspace=tmp_path,
        session_db=tmp_path / "sessions.db",
    )
    monkeypatch.setattr("devx.config.Settings.load", staticmethod(lambda: settings))
    saved_models: list[str] = []

    class FakeUserConfig:
        def set_model(self, model: str) -> None:
            saved_models.append(model)

    monkeypatch.setattr(configuration, "UserConfig", FakeUserConfig)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "data": [
                    {"id": "plain-model", "supported_parameters": []},
                    {"id": "coding-model:free", "supported_parameters": ["tools"]},
                    {"id": "other-tools-model", "supported_parameters": ["tools"]},
                ]
            }).encode()

    monkeypatch.setattr(configuration, "urlopen", lambda request, timeout: Response())

    async def run() -> None:
        async with DevTUI(session_id="integration").run_test() as pilot:
            await submit_command(pilot, "/model live tools")
            assert pilot.app._model_choices == ["coding-model:free", "other-tools-model"]
            assert "coding-model:free [free] [tools]" in pilot.app._transcript[-1]
            await submit_command(pilot, "/model 1")

    asyncio.run(run())
    assert saved_models == ["coding-model:free"]


def test_curated_model_catalog_is_selectable_from_tui(monkeypatch, tmp_path: Path):
    settings = Settings(
        provider="openrouter",
        api_key="test-key",
        base_url="https://openrouter.ai/api/v1",
        model="poolside/laguna-s-2.1:free",
        workspace=tmp_path,
        session_db=tmp_path / "sessions.db",
    )
    monkeypatch.setattr("devx.config.Settings.load", staticmethod(lambda: settings))
    saved_models: list[str] = []

    class FakeUserConfig:
        def set_model(self, model: str) -> None:
            saved_models.append(model)

    monkeypatch.setattr(configuration, "UserConfig", FakeUserConfig)

    async def run() -> None:
        async with DevTUI().run_test() as pilot:
            await submit_command(pilot, "/model catalog")
            expected = [item.identifier for item in configuration.catalog_models("openrouter")]
            assert pilot.app._model_choices == expected
            await submit_command(pilot, "/model 1")

    asyncio.run(run())
    assert saved_models == [configuration.catalog_models("openrouter")[0].identifier]


def test_session_switch_rehydrates_transcript_through_tui(monkeypatch, tmp_path: Path):
    store_path = tmp_path / "sessions.db"
    store = SessionStore(store_path)
    store.save(
        "saved-session",
        "feature work",
        {"messages": [{"role": "user", "content": "Implement the feature"},
                      {"role": "assistant", "content": "Feature implemented"}]},
    )
    settings = Settings(
        provider="openrouter",
        api_key="test-key",
        workspace=tmp_path,
        session_db=store_path,
    )
    monkeypatch.setattr("devx.config.Settings.load", staticmethod(lambda: settings))

    async def run() -> None:
        async with DevTUI(session_id="default").run_test() as pilot:
            await submit_command(pilot, "/session use saved-session")
            assert pilot.app.session_id == "saved-session"
            assert "user: Implement the feature" in pilot.app.chat_text
            assert "assistant: Feature implemented" in pilot.app.chat_text
            assert str(pilot.app.query_one("#session-text").render()) == "session: saved-session"

    asyncio.run(run())


def test_config_show_masks_secret_through_tui(monkeypatch, tmp_path: Path):
    settings = Settings(
        provider="openrouter",
        api_key="sk-or-v1-a-very-real-secret",
        base_url="https://openrouter.ai/api/v1",
        model="coding-model",
        workspace=tmp_path,
    )
    monkeypatch.setattr("devx.config.Settings.load", staticmethod(lambda: settings))

    async def run() -> None:
        async with DevTUI().run_test() as pilot:
            await submit_command(pilot, "/config show")
            output = pilot.app._transcript[-1]
            assert "api_key: sk-o…cret" in output
            assert settings.api_key not in output

    asyncio.run(run())


def test_command_safety_and_config_reload_are_visible(monkeypatch, tmp_path: Path):
    settings = Settings(
        provider="openrouter",
        api_key="test-key",
        model="coding-model",
        workspace=tmp_path,
    )
    monkeypatch.setattr("devx.config.Settings.load", staticmethod(lambda: settings))

    async def run() -> None:
        async with DevTUI().run_test() as pilot:
            await submit_command(pilot, "/config reload")
            assert "future runs: openrouter / coding-model" in pilot.app._transcript[-1]
            count = len(pilot.app._transcript)
            await pilot.press("enter")
            assert len(pilot.app._transcript) == count
            await submit_command(pilot, "/unknown")
            assert pilot.app._transcript[-1] == "Unknown command: /unknown. Type /help for commands."

    asyncio.run(run())


def test_missing_session_reports_error_without_changing_active_session(monkeypatch, tmp_path: Path):
    settings = Settings(
        provider="openrouter",
        api_key="test-key",
        workspace=tmp_path,
        session_db=tmp_path / "sessions.db",
    )
    monkeypatch.setattr("devx.config.Settings.load", staticmethod(lambda: settings))

    async def run() -> None:
        async with DevTUI(session_id="default").run_test() as pilot:
            await submit_command(pilot, "/session use missing-session")
            assert pilot.app.session_id == "default"
            assert pilot.app._transcript[-1] == "Session not found: missing-session"

    asyncio.run(run())


def test_status_bar_displays_provider_and_model_indicator(monkeypatch, tmp_path: Path):
    current_model = "test-model-1"
    settings = Settings(
        provider="openrouter",
        api_key="test-key",
        model=current_model,
        workspace=tmp_path,
        session_db=tmp_path / "sessions.db",
    )
    monkeypatch.setattr("devx.config.Settings.load", staticmethod(lambda **kwargs: settings))

    class FakeUserConfig:
        def set_model(self, model: str) -> None:
            nonlocal settings
            settings = Settings(
                provider="openrouter",
                api_key="test-key",
                model=model,
                workspace=tmp_path,
                session_db=tmp_path / "sessions.db",
            )

    monkeypatch.setattr(configuration, "UserConfig", FakeUserConfig)

    async def run() -> None:
        async with DevTUI(session_id="default").run_test() as pilot:
            model_widget = pilot.app.query_one("#model-text", Static)
            assert "openrouter:test-model-1" in str(model_widget.render())

            # Now switch model using /model use
            await submit_command(pilot, "/model use new-coder-model")
            assert "openrouter:new-coder-model" in str(model_widget.render())

    asyncio.run(run())


def test_config_reset_restores_defaults_and_updates_status_bar(monkeypatch, tmp_path: Path):
    # Setup custom settings initially
    active_settings = Settings(
        provider="openrouter",
        api_key="custom-key",
        model="custom-model",
        workspace=tmp_path,
        session_db=tmp_path / "sessions.db",
    )

    def fake_load(**kwargs):
        return active_settings

    monkeypatch.setattr("devx.config.Settings.load", staticmethod(fake_load))

    reset_called = False

    class FakeUserConfig:
        def __init__(self, path=None):
            pass

        def reset(self, keys=None):
            nonlocal reset_called, active_settings
            reset_called = True
            # Simulate reset restoring defaults
            active_settings = Settings(
                provider="openai",
                api_key="sk-placeholder",
                model="gpt-4o",
                workspace=tmp_path,
                session_db=tmp_path / "sessions.db",
            )

    monkeypatch.setattr(configuration, "UserConfig", FakeUserConfig)

    async def run() -> None:
        async with DevTUI(session_id="default").run_test() as pilot:
            model_widget = pilot.app.query_one("#model-text", Static)
            assert "openrouter:custom-model" in str(model_widget.render())

            await submit_command(pilot, "/config reset")
            assert reset_called is True
            assert "Configuration reset to defaults: openai / gpt-4o" in pilot.app._transcript[-1]
            assert "openai:gpt-4o" in str(model_widget.render())

    asyncio.run(run())


def test_plan_command_triggers_runner_plan_only(monkeypatch, tmp_path: Path):
    plan_called: list[tuple[str, bool]] = []
    settings = Settings(api_key="test-key", workspace=tmp_path, session_db=tmp_path / "sessions.db")
    monkeypatch.setattr("devx.config.Settings.load", staticmethod(lambda **kwargs: settings))

    async def fake_ask(self, task: str, *, plan_only: bool = False, plan_first: bool = True):
        plan_called.append((task, plan_only))
        self.app._run_active = False
        self.app._set_running(False)

    monkeypatch.setattr("devx.tui.components.runner.AgentRunner.ask", fake_ask)

    async def run() -> None:
        async with DevTUI(session_id="default").run_test() as pilot:
            await submit_command(pilot, "/plan build auth service")
            assert plan_called == [("build auth service", True)]
            assert "> build auth service (plan only)" in pilot.app._transcript[-1]

    asyncio.run(run())


