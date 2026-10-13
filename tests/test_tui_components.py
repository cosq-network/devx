"""Focused logic tests for TUI components (no Textual app instance required).

The components are exercised through a lightweight ``FakeApp`` that records
``_write``/``_log`` output and widget updates, so the tests stay offline and
do not need a running terminal.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from devx.config import Settings
from devx.configuration import ConfigurationError, ModelInfo


# ── Fake app / widgets ───────────────────────────────────────────────────

class FakeWidget:
    def __init__(self):
        self._value = None
        self._cleared = 0
        self._lines = []

    def update(self, text=None):
        self._value = text

    def clear(self):
        self._cleared += 1

    def write(self, line):
        self._lines.append(str(line))


class FakeRunner:
    def __init__(self):
        self.calls = []

    async def ask(self, value, *, plan_only=False, plan_first=True):
        self.calls.append((value, plan_only, plan_first))


class FakeApp:
    DOUBLE_CTRL_C_WINDOW = 1.5

    def __init__(self):
        self._written: list[str] = []
        self._log_lines: list[str] = []
        self._transcript: list[str] = []
        self.session_id = "sess-1"
        self._remote_backend = None
        self._remote_ssh = ""
        self._workspace = None
        self._cancellation = None
        self._run_active = False
        self._requested_agent = "supervisor"
        self._finish_reconfiguration = None
        self._widgets: dict[str, FakeWidget] = {}
        self._pushed_screens: list = []
        self._workers: list = []
        self._running = None
        self.runner = FakeRunner()
        # Double-press Ctrl+C-to-quit state (see DOUBLE_CTRL_C_WINDOW).
        self._ctrl_c_armed = False
        self._ctrl_c_at = 0.0
        self.quit_calls = 0
        self.timers = []

    def _write(self, text):
        self._written.append(text)

    def _log(self, text):
        self._log_lines.append(text)

    def update_model_status(self):
        pass

    def query_one(self, query, cls=None):
        key = str(query)
        if key not in self._widgets:
            self._widgets[key] = FakeWidget()
        return self._widgets[key]

    def push_screen(self, screen):
        self._pushed_screens.append(screen)

    def run_worker(self, coro, **kwargs):
        self._workers.append(coro)
        # The fake never executes workers; close un-awaited coroutines so pytest
        # does not report "coroutine was never awaited" warnings.
        coro.close()

    def _set_running(self, value):
        self._running = value

    def action_quit(self):
        self.quit_calls += 1
        self._written.append("quit")

    def set_timer(self, delay, callback):
        self.timers.append((delay, callback))


class FakeCancellation:
    def __init__(self):
        self.cancel_calls = 0

    def cancel(self):
        self.cancel_calls += 1


@pytest.fixture
def fake_app():
    return FakeApp()


def patch_settings(monkeypatch, settings):
    """Replace Settings.load with a no-argument stub (matches component usage)."""
    monkeypatch.setattr(Settings, "load", staticmethod(lambda: settings))


def patch_settings_workspace(monkeypatch, settings_factory):
    """Replace Settings.load with one accepting ``workspace`` (reload/reset)."""

    def load(workspace=None):
        return settings_factory(workspace=workspace)

    monkeypatch.setattr(Settings, "load", staticmethod(load))


def fake_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        workspace=tmp_path,
        session_db=tmp_path / "s.db",
        cache_db=tmp_path / "c.db",
        provider="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-4o",
        api_key="sk-1234567890abcdef",
    )
    values.update(overrides)
    return Settings(**values)


# ── HelpSystem ───────────────────────────────────────────────────────────

class TestHelpSystem:
    def test_quick_start_help(self, fake_app):
        from devx.tui.components.commands import HelpSystem

        HelpSystem(fake_app).show_help()
        assert fake_app._written[-1].startswith("QUICK START")

    @pytest.mark.parametrize("topic", ["commands", "command", "all"])
    def test_aliases_show_quick_start(self, fake_app, topic):
        from devx.tui.components.commands import HelpSystem
        from devx.tui.components.commands import QUICK_START_HELP

        HelpSystem(fake_app).show_help(topic)
        assert fake_app._written[-1] == QUICK_START_HELP

    def test_known_topic(self, fake_app):
        from devx.tui.components.commands import HelpSystem
        from devx.tui.components.commands import HELP_TOPICS

        HelpSystem(fake_app).show_help("setup")
        assert fake_app._written[-1] == HELP_TOPICS["setup"]

    def test_unknown_topic(self, fake_app):
        from devx.tui.components.commands import HelpSystem

        HelpSystem(fake_app).show_help("bogus")
        assert fake_app._written[-1] == "No help topic: bogus. Try /help."

    def test_topic_whitespace_and_case_normalized(self, fake_app):
        from devx.tui.components.commands import HelpSystem

        HelpSystem(fake_app).show_help("  MODEL  ")
        assert "MODEL" in fake_app._written[-1]
        assert "No help topic" not in fake_app._written[-1]


# ── ProviderManager ──────────────────────────────────────────────────────

class FakeUserConfig:
    def __init__(self, *args, **kwargs):
        self.calls = []

    def set_provider(self, provider):
        self.calls.append(("set_provider", provider))

    def set_model(self, model):
        self.calls.append(("set_model", model))

    def set_api_key(self, api_key):
        self.calls.append(("set_api_key", api_key))


class RecordingUserConfig(FakeUserConfig):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.resets = []

    def reset(self, *args, **kwargs):
        self.resets.append((args, kwargs))


class TestProviderManager:
    def test_show_providers_lists_choices(self, fake_app):
        from devx.tui.components.commands import ProviderManager

        manager = ProviderManager(fake_app)
        manager.show_providers()
        text = fake_app._written[-1]
        assert text.startswith("Providers (use /provider <number> or /provider <name>):")
        assert "\n1. " in text
        assert manager._provider_choices == sorted(manager._provider_choices)

    def test_select_provider_by_number(self, fake_app, monkeypatch):
        monkeypatch.setattr("devx.configuration.UserConfig", FakeUserConfig)
        from devx.tui.components.commands import ProviderManager

        manager = ProviderManager(fake_app)
        manager.show_providers()
        manager.select_provider("1")
        chosen = manager._provider_choices[0]
        assert fake_app._written[-1] == f"Provider saved: {chosen}. New runs will use it."

    def test_select_provider_by_name(self, fake_app, monkeypatch):
        monkeypatch.setattr("devx.configuration.UserConfig", FakeUserConfig)
        from devx.tui.components.commands import ProviderManager

        manager = ProviderManager(fake_app)
        manager.show_providers()
        name = manager._provider_choices[0]
        manager.select_provider(name)
        assert fake_app._written[-1].startswith(f"Provider saved: {name}.")

    def test_select_provider_out_of_range(self, fake_app):
        from devx.tui.components.commands import ProviderManager

        manager = ProviderManager(fake_app)
        manager.show_providers()
        manager.select_provider("99")
        assert fake_app._written[-1] == "Choose a provider number from the latest /provider list."

    def test_select_provider_zero(self, fake_app):
        from devx.tui.components.commands import ProviderManager

        manager = ProviderManager(fake_app)
        manager.show_providers()
        manager.select_provider("0")
        assert fake_app._written[-1] == "Choose a provider number from the latest /provider list."

    def test_configuration_error_is_written(self, fake_app, monkeypatch):
        class RaisingConfig:
            def __init__(self, *args, **kwargs):
                pass

            def set_provider(self, provider):
                raise ConfigurationError(f"Unknown provider: {provider}")

        monkeypatch.setattr("devx.configuration.UserConfig", RaisingConfig)
        from devx.tui.components.commands import ProviderManager

        ProviderManager(fake_app).select_provider("bogus-provider")
        assert fake_app._written[-1] == "Unknown provider: bogus-provider"


# ── ModelManager ─────────────────────────────────────────────────────────

class TestModelManager:
    def test_select_by_number_with_empty_choices(self, fake_app):
        from devx.tui.components.commands import ModelManager

        ModelManager(fake_app).select_model("3")
        assert fake_app._written[-1] == "Choose a model number from the latest /model list."

    def test_select_empty_value(self, fake_app):
        from devx.tui.components.commands import ModelManager

        ModelManager(fake_app).select_model("")
        assert fake_app._written[-1] == "Enter a model name or run /model first."

    def test_select_by_name_saves(self, fake_app, monkeypatch):
        monkeypatch.setattr("devx.configuration.UserConfig", FakeUserConfig)
        from devx.tui.components.commands import ModelManager

        ModelManager(fake_app).select_model("gpt-4o")
        assert fake_app._written[-1] == "Model saved: gpt-4o. New runs will use it."

    def test_select_by_number(self, fake_app, monkeypatch):
        monkeypatch.setattr("devx.configuration.UserConfig", FakeUserConfig)
        from devx.tui.components.commands import ModelManager

        manager = ModelManager(fake_app)
        manager._model_choices = ["gpt-4o", "gpt-4o-mini"]
        manager.select_model("2")
        assert fake_app._written[-1] == "Model saved: gpt-4o-mini. New runs will use it."

    def test_select_number_out_of_range(self, fake_app):
        from devx.tui.components.commands import ModelManager

        manager = ModelManager(fake_app)
        manager._model_choices = ["gpt-4o"]
        manager.select_model("5")
        assert fake_app._written[-1] == "Choose a model number from the latest /model list."

    def test_show_catalog_curated_openai(self, fake_app, monkeypatch, tmp_path):
        patch_settings(monkeypatch, fake_settings(tmp_path, provider="openai"))
        from devx.tui.components.commands import ModelManager

        manager = ModelManager(fake_app)
        manager.show_catalog()
        text = fake_app._written[-1]
        assert text.startswith("Curated coding models for openai")
        assert " [tools, coding]" in text
        assert manager._model_choices
        assert len(manager._model_choices) == 5

    def test_show_catalog_filter(self, fake_app, monkeypatch, tmp_path):
        patch_settings(monkeypatch, fake_settings(tmp_path, provider="openai"))
        from devx.tui.components.commands import ModelManager

        manager = ModelManager(fake_app)
        manager.show_catalog("gpt-4o")
        assert manager._model_choices == ["gpt-4o", "gpt-4o-mini"]

    def test_show_catalog_empty_provider(self, fake_app, monkeypatch, tmp_path):
        patch_settings(monkeypatch, fake_settings(tmp_path, provider="openrouter"))
        monkeypatch.setattr("devx.tui.components.commands.catalog_models", lambda provider: [])
        from devx.tui.components.commands import ModelManager

        manager = ModelManager(fake_app)
        manager.show_catalog()
        assert fake_app._written[-1] == "No curated coding models found for provider: openrouter."
        assert manager._model_choices == []

    def test_show_models_live(self, fake_app, monkeypatch, tmp_path):
        patch_settings(monkeypatch, fake_settings(tmp_path))
        live = [
            ModelInfo("m1", free=True, tool_calling=True, coding=True),
            ModelInfo("m2", tool_calling=True, coding=True),
            ModelInfo("m3"),
        ]
        monkeypatch.setattr("devx.tui.components.commands.list_models", lambda base_url, api_key: live)
        from devx.tui.components.commands import ModelManager

        manager = ModelManager(fake_app)
        manager.show_models()
        text = fake_app._written[-1]
        assert "m1" in text
        assert "[free]" in text
        assert "[tools]" in text
        assert manager._model_choices == ["m1", "m2", "m3"]

    def test_show_models_filter_free(self, fake_app, monkeypatch, tmp_path):
        patch_settings(monkeypatch, fake_settings(tmp_path))
        live = [
            ModelInfo("m1", free=True, tool_calling=True, coding=True),
            ModelInfo("m2", tool_calling=True, coding=True),
        ]
        monkeypatch.setattr("devx.tui.components.commands.list_models", lambda base_url, api_key: live)
        from devx.tui.components.commands import ModelManager

        manager = ModelManager(fake_app)
        manager.show_models("free")
        assert manager._model_choices == ["m1"]

    def test_show_models_filter_tools(self, fake_app, monkeypatch, tmp_path):
        patch_settings(monkeypatch, fake_settings(tmp_path))
        live = [
            ModelInfo("m1", tool_calling=False, coding=True),
            ModelInfo("m2", tool_calling=True, coding=True),
        ]
        monkeypatch.setattr("devx.tui.components.commands.list_models", lambda base_url, api_key: live)
        from devx.tui.components.commands import ModelManager

        manager = ModelManager(fake_app)
        manager.show_models("tools")
        assert manager._model_choices == ["m2"]

    def test_show_models_empty_result(self, fake_app, monkeypatch, tmp_path):
        patch_settings(monkeypatch, fake_settings(tmp_path))
        monkeypatch.setattr("devx.tui.components.commands.list_models", lambda base_url, api_key: [])
        from devx.tui.components.commands import ModelManager

        ModelManager(fake_app).show_models()
        assert fake_app._written[-1] == "No matching models returned by the active provider."

    def test_show_models_error(self, fake_app, monkeypatch, tmp_path):
        patch_settings(monkeypatch, fake_settings(tmp_path))

        def failing(base_url, api_key):
            raise ConfigurationError("provider returned an error")

        monkeypatch.setattr("devx.tui.components.commands.list_models", failing)
        from devx.tui.components.commands import ModelManager

        ModelManager(fake_app).show_models()
        assert fake_app._written[-1] == "provider returned an error"


# ── SessionManager ───────────────────────────────────────────────────────

class StubSessionStore:
    instances = []
    list_result = [{"id": "s1", "name": "first"}, {"id": "s2", "name": "second"}]
    load_result = {"id": "s1", "name": "first", "state": {"messages": [
        {"role": "user", "content": "hi"},
        {"role": "x", "content": "nope"},
    ]}}
    rename_result = True
    export_result = None
    import_result = "imported-1"
    raise_on_list = False
    raise_on_load = False
    raise_on_rename = False
    raise_on_export = False
    raise_on_import = False

    def __init__(self, db):
        self.db = db
        StubSessionStore.instances.append(self)

    def list_sessions(self):
        if self.raise_on_list:
            raise sqlite3.OperationalError("db locked")
        return self.list_result

    def load(self, requested):
        if self.raise_on_load:
            raise sqlite3.OperationalError("db locked")
        # A configured result is returned for any requested id/name; the caller
        # decides meaning. Tests set ``load_result=None`` to simulate a miss.
        if self.load_result is not None:
            return self.load_result
        return None

    def rename(self, session_id, name):
        if self.raise_on_rename:
            raise sqlite3.OperationalError("db locked")
        return self.rename_result

    def export_session(self, session_id, path):
        if self.raise_on_export:
            raise ValueError("bad destination")
        return self.export_result if self.export_result is not None else path

    def import_session(self, path, name=None):
        if self.raise_on_import:
            raise KeyError("duplicate")
        return self.import_result


@pytest.fixture
def stub_session_store(monkeypatch):
    StubSessionStore.instances = []
    StubSessionStore.list_result = [
        {"id": "s1", "name": "first"},
        {"id": "s2", "name": "second"},
    ]
    StubSessionStore.load_result = {
        "id": "s1",
        "name": "first",
        "state": {"messages": [
            {"role": "user", "content": "hi"},
            {"role": "x", "content": "nope"},
        ]},
    }
    StubSessionStore.rename_result = True
    StubSessionStore.export_result = None
    StubSessionStore.import_result = "imported-1"
    StubSessionStore.raise_on_list = False
    StubSessionStore.raise_on_load = False
    StubSessionStore.raise_on_rename = False
    StubSessionStore.raise_on_export = False
    StubSessionStore.raise_on_import = False
    monkeypatch.setattr("devx.harness.session.SessionStore", StubSessionStore)
    return StubSessionStore


class TestSessionManager:
    def test_list_sessions(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.session import SessionManager

        SessionManager(fake_app).list_sessions()
        assert fake_app._written[-1] == "s1  first\ns2  second"

    def test_list_sessions_empty(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        stub_session_store.list_result = []
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.session import SessionManager

        SessionManager(fake_app).list_sessions()
        assert fake_app._written[-1] == "No saved sessions."

    def test_list_sessions_error(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        stub_session_store.raise_on_list = True
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.session import SessionManager

        SessionManager(fake_app).list_sessions()
        assert "Unable to list sessions: db locked" in fake_app._written[-1]

    def test_switch_session(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.session import SessionManager

        SessionManager(fake_app).switch_session("s1")
        assert fake_app.session_id == "s1"
        # Every dict message with string content is kept, regardless of role.
        assert fake_app._transcript == ["user: hi", "x: nope"]
        assert fake_app._written[-1] == "Switched to session: first"
        chat = fake_app.query_one("#chat-view")
        assert chat._cleared == 1
        assert chat._lines == ["user: hi", "x: nope"]
        assert fake_app.query_one("#session-text")._value == "session: s1"

    def test_switch_session_not_found(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        stub_session_store.load_result = None
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.session import SessionManager

        SessionManager(fake_app).switch_session("missing")
        assert fake_app._written[-1] == "Session not found: missing"

    def test_switch_session_empty(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.session import SessionManager

        SessionManager(fake_app).switch_session("")
        assert fake_app._written[-1] == "Usage: /session use <id-or-name>"

    def test_switch_session_db_error(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        stub_session_store.raise_on_load = True
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.session import SessionManager

        SessionManager(fake_app).switch_session("s1")
        assert "Unable to load session: db locked" in fake_app._written[-1]

    def test_rename_session(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.session import SessionManager

        SessionManager(fake_app).rename_session("new-name")
        assert fake_app._written[-1] == "Renamed session to: new-name"

    def test_rename_session_not_found(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        stub_session_store.rename_result = False
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.session import SessionManager

        SessionManager(fake_app).rename_session("new-name")
        assert fake_app._written[-1] == "Session not found."

    def test_rename_session_error(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        stub_session_store.raise_on_rename = True
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.session import SessionManager

        SessionManager(fake_app).rename_session("x")
        assert "Unable to rename session: db locked" in fake_app._written[-1]

    def test_export_session(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.session import SessionManager

        SessionManager(fake_app).export_session("out.json")
        assert fake_app._written[-1] == f"Exported session: {Path('out.json')}"

    def test_export_session_error(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        stub_session_store.raise_on_export = True
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.session import SessionManager

        SessionManager(fake_app).export_session("out.json")
        assert "Export failed: bad destination" in fake_app._written[-1]

    def test_import_session(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.session import SessionManager

        SessionManager(fake_app).import_session("in.json")
        assert fake_app.session_id == "imported-1"
        assert fake_app._written[-1] == "Imported session: imported-1"

    def test_import_session_error(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        stub_session_store.raise_on_import = True
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.session import SessionManager

        SessionManager(fake_app).import_session("in.json")
        assert "Import failed" in fake_app._written[-1]
        assert "'duplicate'" in fake_app._written[-1]


# ── ActionHandlers ───────────────────────────────────────────────────────

class TestActionHandlers:
    def test_cancel_run_no_active(self, fake_app):
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).cancel_run()
        assert fake_app._written[-1] == "No active run to cancel."

    def test_cancel_run_active(self, fake_app):
        from devx.tui.components.actions import ActionHandlers

        cancellation = FakeCancellation()
        fake_app._cancellation = cancellation
        ActionHandlers(fake_app).cancel_run()
        assert cancellation.cancel_calls == 1
        assert fake_app._written[-1] == "Cancellation requested."

    def test_clear_chat(self, fake_app):
        from devx.tui.components.actions import ActionHandlers

        fake_app._transcript = ["a", "b"]
        ActionHandlers(fake_app).clear_chat()
        assert fake_app.query_one("#chat-view")._cleared == 1
        assert fake_app._transcript == []
        assert fake_app._written[-1] == "Chat cleared. Ready for your next task."

    def test_copy_transcript_success(self, fake_app, monkeypatch):
        monkeypatch.setattr(
            "devx.tui.components.modals.copy_to_clipboard", lambda text, app=None: None
        )
        fake_app._transcript = ["line1"]
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).copy_transcript()
        assert fake_app.query_one("#status-text")._value == "● copied transcript to clipboard"

    def test_copy_transcript_empty_errors(self, fake_app):
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).copy_transcript()
        assert "copy failed" in fake_app.query_one("#status-text")._value

    def test_copy_or_cancel_copies_when_idle(self, fake_app, monkeypatch):
        monkeypatch.setattr(
            "devx.tui.components.modals.copy_to_clipboard", lambda text, app=None: None
        )
        fake_app._transcript = ["x"]
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).copy_or_cancel()
        assert "● copied transcript to clipboard" in fake_app.query_one("#status-text")._value
        assert fake_app._ctrl_c_armed is True
        assert fake_app._ctrl_c_at > 0.0

    def test_copy_or_cancel_empty_transcript_shows_copy_failure(self, fake_app):
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).copy_or_cancel()
        assert "copy failed" in fake_app.query_one("#status-text")._value
        assert fake_app._ctrl_c_armed is True

    def test_copy_or_cancel_cancels_active_run(self, fake_app):
        cancellation = FakeCancellation()
        fake_app._run_active = True
        fake_app._cancellation = cancellation
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).copy_or_cancel()
        assert cancellation.cancel_calls == 1
        assert fake_app._written[-1] == "Cancellation requested."
        assert fake_app._ctrl_c_armed is True

    def test_copy_or_cancel_second_press_quits_within_window(self, fake_app, monkeypatch):
        steps = iter([100.0, 100.6])
        monkeypatch.setattr(
            "devx.tui.components.actions.time.monotonic", lambda: next(steps)
        )
        fake_app._transcript = ["x"]
        monkeypatch.setattr(
            "devx.tui.components.modals.copy_to_clipboard", lambda text, app=None: None
        )
        from devx.tui.components.actions import ActionHandlers

        handlers = ActionHandlers(fake_app)
        handlers.copy_or_cancel()
        assert fake_app._ctrl_c_armed is True
        handlers.copy_or_cancel()
        assert fake_app.quit_calls == 1
        assert fake_app._ctrl_c_armed is False

    def test_copy_or_cancel_second_press_outside_window_rearms(self, fake_app, monkeypatch):
        steps = iter([100.0, 103.0, 103.2])
        monkeypatch.setattr(
            "devx.tui.components.actions.time.monotonic", lambda: next(steps)
        )
        fake_app._transcript = ["x"]
        monkeypatch.setattr(
            "devx.tui.components.modals.copy_to_clipboard", lambda text, app=None: None
        )
        from devx.tui.components.actions import ActionHandlers

        handlers = ActionHandlers(fake_app)
        handlers.copy_or_cancel()
        handlers.copy_or_cancel()  # outside the 1.5 s window: no quit, re-arm
        assert fake_app.quit_calls == 0
        assert fake_app._ctrl_c_armed is True
        assert fake_app._ctrl_c_at == 103.0
        handlers.copy_or_cancel()  # a fast press after re-arming quits
        assert fake_app.quit_calls == 1

    def test_copy_or_cancel_schedules_disarm(self, fake_app, monkeypatch):
        fake_app._transcript = ["x"]
        monkeypatch.setattr(
            "devx.tui.components.modals.copy_to_clipboard", lambda text, app=None: None
        )
        from devx.tui.components.actions import ActionHandlers

        handlers = ActionHandlers(fake_app)
        handlers.copy_or_cancel()
        assert len(fake_app.timers) == 1
        delay, callback = fake_app.timers[0]
        assert delay == fake_app.DOUBLE_CTRL_C_WINDOW
        callback()
        assert fake_app._ctrl_c_armed is False

    def test_set_agent_valid(self, fake_app):
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).set_agent("coder")
        assert fake_app._requested_agent == "coder"
        assert fake_app.query_one("#status-text")._value == "● agent: coder"

    def test_set_agent_invalid(self, fake_app):
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).set_agent("hacker")
        assert fake_app._written[-1] == "Choose: supervisor, planner, coder, or tester"
        assert fake_app._requested_agent == "supervisor"

    def test_set_agent_empty_defaults_supervisor(self, fake_app):
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).set_agent("")
        assert fake_app._requested_agent == "supervisor"

    def test_run_plan_usage(self, fake_app):
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).run_plan("")
        assert fake_app._written[-1] == "Usage: /plan <task>"

    def test_run_plan_rejects_when_active(self, fake_app):
        fake_app._run_active = True
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).run_plan("task")
        assert fake_app._written[-1] == (
            "A run is already active. Use /cancel before starting another task."
        )

    def test_run_plan_starts_worker(self, fake_app):
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).run_plan("improve docs")
        assert fake_app._run_active is True
        assert fake_app._running is True
        assert len(fake_app._workers) == 1
        assert fake_app._written[-1] == "> improve docs (plan only)"

    def test_show_config_masks_api_key(self, fake_app, monkeypatch, tmp_path):
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).show_config()
        text = fake_app._written[-1]
        assert "provider: openai" in text
        assert "api_key: " in text
        assert "sk-1234567890abcdef" not in text
        assert "…" in text

    def test_show_config_unset_key(self, fake_app, monkeypatch, tmp_path):
        patch_settings(monkeypatch, fake_settings(tmp_path, api_key="sk-placeholder"))
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).show_config()
        assert "<not configured>" in fake_app._written[-1]

    def test_reload_config(self, fake_app, monkeypatch, tmp_path):
        patch_settings_workspace(
            monkeypatch,
            lambda workspace=None: fake_settings(tmp_path, workspace=workspace or tmp_path),
        )
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).reload_config()
        assert fake_app._workspace == tmp_path
        assert "Configuration reloaded" in fake_app._written[-1]

    def test_reset_config(self, fake_app, monkeypatch, tmp_path):
        resets: list = []
        user_config = RecordingUserConfig()
        user_config.resets = resets
        monkeypatch.setattr("devx.configuration.UserConfig", type("UC", (), {
            "__init__": lambda self, *a, **k: None,
            "reset": lambda self, *a, **k: resets.append((a, k)),
        }))
        (tmp_path / ".env").write_text("DEVX_PROVIDER=openai", encoding="utf-8")
        fake_app._workspace = tmp_path
        patch_settings_workspace(
            monkeypatch,
            lambda workspace=None: fake_settings(tmp_path, workspace=workspace or tmp_path),
        )
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).reset_config()
        assert len(resets) == 2  # user config + workspace .env
        assert "Configuration reset to defaults" in fake_app._written[-1]
        assert fake_app._workspace == tmp_path
        assert "Use /setup or /provider" in fake_app._written[-1]

    def test_set_api_key_pushes_modal(self, fake_app, monkeypatch):
        monkeypatch.setattr("devx.configuration.UserConfig", FakeUserConfig)
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).set_api_key()
        assert len(fake_app._pushed_screens) == 1

    def test_show_diff(self, fake_app, monkeypatch, tmp_path):
        class StubWorkspaceTools:
            def __init__(self, policy, **kwargs):
                self.policy = policy

            def git_diff(self):
                return SimpleNamespace(output="M src/a.py")

        patch_settings(monkeypatch, fake_settings(tmp_path))
        monkeypatch.setattr("devx.harness.tools.WorkspaceTools", StubWorkspaceTools)
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).show_diff()
        assert fake_app._written[-1] == "M src/a.py"

    def test_show_diff_empty_output(self, fake_app, monkeypatch, tmp_path):
        class StubWorkspaceTools:
            def __init__(self, policy, **kwargs):
                pass

            def git_diff(self):
                return SimpleNamespace(output="")

        patch_settings(monkeypatch, fake_settings(tmp_path))
        monkeypatch.setattr("devx.harness.tools.WorkspaceTools", StubWorkspaceTools)
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).show_diff()
        assert fake_app._written[-1] == "No uncommitted changes."

    def test_undo_last_run_no_completed_run(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        stub_session_store.load_result = {"metadata": {}}
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).undo_last_run()
        assert fake_app._written[-1] == "No completed run is available to undo."

    def test_undo_last_run_restores(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        stub_session_store.load_result = {"metadata": {"last_run_id": "run-9"}}
        rollback_calls = []

        class StubChangeJournal:
            def __init__(self, db):
                self.db = db

            def rollback(self, run_id, backend=None):
                rollback_calls.append((run_id, backend))
                return ["src/a.py"]

        monkeypatch.setattr("devx.harness.changes.ChangeJournal", StubChangeJournal)
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).undo_last_run()
        assert fake_app._written[-1] == "Restored: src/a.py"
        assert rollback_calls[-1][0] == "run-9"

    def test_undo_last_run_restores_empty(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        stub_session_store.load_result = {"metadata": {"last_run_id": "run-9"}}

        class EmptyJournal:
            def __init__(self, db):
                pass

            def rollback(self, run_id, backend=None):
                return []

        monkeypatch.setattr("devx.harness.changes.ChangeJournal", EmptyJournal)
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).undo_last_run()
        assert fake_app._written[-1] == "No safe changes to restore."

    def test_undo_last_run_load_error(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        stub_session_store.raise_on_load = True
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).undo_last_run()
        assert "Unable to load session: db locked" in fake_app._written[-1]

    def test_undo_last_run_rollback_error(self, fake_app, monkeypatch, tmp_path, stub_session_store):
        stub_session_store.load_result = {"metadata": {"last_run_id": "run-9"}}

        class RaisingJournal:
            def __init__(self, db):
                pass

            def rollback(self, run_id, backend=None):
                raise sqlite3.OperationalError("locked")

        monkeypatch.setattr("devx.harness.changes.ChangeJournal", RaisingJournal)
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).undo_last_run()
        assert "Unable to undo changes: locked" in fake_app._written[-1]

    def test_connect_remote_usage(self, fake_app):
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).connect_remote("")
        assert fake_app._written[-1].startswith("Usage: /remote connect")

    def test_connect_remote_ok(self, fake_app, monkeypatch, tmp_path):
        class FakeSshBackend:
            def __init__(self, cfg):
                self.cfg = cfg
                self.closed = False

            def close(self):
                self.closed = True

        patch_settings(monkeypatch, fake_settings(tmp_path))
        monkeypatch.setattr("devx.harness.ssh_backend.SshWorkspaceBackend", FakeSshBackend)
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).connect_remote("user@example.com:/home/user/proj")
        backend = fake_app._remote_backend
        assert isinstance(backend, FakeSshBackend)
        assert fake_app._remote_ssh == "user@example.com:/home/user/proj"
        assert backend.cfg.username == "user"
        assert backend.cfg.host == "example.com"
        assert "Connected remote SSH workspace: user@example.com" in fake_app._written[-1]
        assert "example.com" in fake_app.query_one("#status-text")._value

    def test_connect_remote_invalid_uri(self, fake_app, monkeypatch, tmp_path):
        monkeypatch.setattr("devx.harness.ssh_backend.SshWorkspaceBackend", object)
        patch_settings(monkeypatch, fake_settings(tmp_path))
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).connect_remote("not-a-uri")
        assert "Remote connection error" in fake_app._written[-1]

    def test_connect_remote_closes_existing_backend(self, fake_app, monkeypatch, tmp_path):
        class FakeSshBackend:
            def __init__(self, cfg):
                self.cfg = cfg
                self.closed = False

            def close(self):
                self.closed = True

        patch_settings(monkeypatch, fake_settings(tmp_path))
        monkeypatch.setattr("devx.harness.ssh_backend.SshWorkspaceBackend", FakeSshBackend)
        previous = FakeSshBackend(object())
        fake_app._remote_backend = previous
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).connect_remote("user@example.com:/p")
        assert previous.closed is True

    def test_disconnect_remote(self, fake_app, monkeypatch, tmp_path):
        class FakeSshBackend:
            def __init__(self, cfg):
                self.cfg = cfg
                self.closed = False

            def close(self):
                self.closed = True

        patch_settings(monkeypatch, fake_settings(tmp_path))
        backend = FakeSshBackend(object())
        fake_app._remote_backend = backend
        fake_app._remote_ssh = "user@host:/p"
        from devx.tui.components.actions import ActionHandlers

        ActionHandlers(fake_app).disconnect_remote()
        assert backend.closed is True
        assert fake_app._remote_backend is None
        assert fake_app._remote_ssh == ""
        assert fake_app._written[-1] == (
            "Disconnected remote workspace. Reverted to local workspace."
        )
        assert fake_app.query_one("#status-text")._value == "● ready"


# ── copy_to_clipboard (OS-default clipboard) ─────────────────────────────

class TestCopyToClipboard:
    def test_empty_transcript_raises(self):
        from devx.tui.components.modals import copy_to_clipboard

        with pytest.raises(RuntimeError, match="transcript is empty"):
            copy_to_clipboard("")

    def test_no_utility_found_raises(self, monkeypatch):
        monkeypatch.setattr("devx.util.clipboard.platform.system", lambda: "Linux")
        monkeypatch.setattr("devx.util.clipboard.shutil.which", lambda name: None)
        monkeypatch.setattr("devx.util.clipboard.tk_clipboard_copy", lambda text: False)
        from devx.tui.components.modals import copy_to_clipboard

        with pytest.raises(RuntimeError, match="No clipboard utility found"):
            copy_to_clipboard("text")

    def test_windows_uses_clip(self, monkeypatch):
        calls = []
        monkeypatch.setattr("devx.util.clipboard.platform.system", lambda: "Windows")
        monkeypatch.setattr(
            "devx.util.clipboard.shutil.which",
            lambda name: "clip.exe" if name == "clip.exe" else None,
        )

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))

        monkeypatch.setattr("devx.util.clipboard.subprocess.run", fake_run)
        from devx.tui.components.modals import copy_to_clipboard

        label = copy_to_clipboard("hello")
        assert label == "OS clipboard"
        assert len(calls) == 1
        command, kwargs = calls[0]
        assert command == ["clip.exe"]
        assert kwargs["input"] == "hello"
        assert kwargs["text"] is True
        assert kwargs["check"] is True

    def test_darwin_uses_pbcopy(self, monkeypatch):
        calls = []
        monkeypatch.setattr("devx.util.clipboard.platform.system", lambda: "Darwin")
        monkeypatch.setattr(
            "devx.util.clipboard.shutil.which",
            lambda name: "pbcopy" if name == "pbcopy" else None,
        )

        def fake_run(command, **kwargs):
            calls.append(command)

        monkeypatch.setattr("devx.util.clipboard.subprocess.run", fake_run)
        from devx.tui.components.modals import copy_to_clipboard

        assert copy_to_clipboard("hello") == "OS clipboard"
        assert calls == [["pbcopy"]]

    def test_uses_osc52_fallback_when_app_present(self, monkeypatch):
        monkeypatch.setattr("devx.util.clipboard.platform.system", lambda: "Linux")
        monkeypatch.setattr("devx.util.clipboard.shutil.which", lambda name: None)
        monkeypatch.setattr("devx.util.clipboard.tk_clipboard_copy", lambda text: False)
        copied = []

        class FakeClippyApp:
            def copy_to_clipboard(self, text):
                copied.append(text)

        from devx.tui.components.modals import copy_to_clipboard

        label = copy_to_clipboard("hello", app=FakeClippyApp())
        assert label == "terminal clipboard (OSC 52)"
        assert copied == ["hello"]

    def test_tkinter_fallback(self, monkeypatch):
        monkeypatch.setattr("devx.util.clipboard.platform.system", lambda: "Linux")
        monkeypatch.setattr("devx.util.clipboard.shutil.which", lambda name: None)
        monkeypatch.setattr("devx.util.clipboard.tk_clipboard_copy", lambda text: True)
        from devx.tui.components.modals import copy_to_clipboard

        assert copy_to_clipboard("hello") == "OS clipboard (Tk)"

    def test_osc52_encode_golden(self):
        from devx.util.clipboard import osc52_encode

        assert osc52_encode("hello") == "\x1b]52;c;aGVsbG8=\a"

    def test_run_failure_falls_through_to_raise(self, monkeypatch):
        monkeypatch.setattr("devx.util.clipboard.platform.system", lambda: "Windows")
        monkeypatch.setattr(
            "devx.util.clipboard.shutil.which",
            lambda name: "clip.exe" if name == "clip.exe" else None,
        )

        def failing(command, **kwargs):
            raise OSError("pipe broken")

        monkeypatch.setattr("devx.util.clipboard.subprocess.run", failing)
        monkeypatch.setattr("devx.util.clipboard.tk_clipboard_copy", lambda text: False)
        from devx.tui.components.modals import copy_to_clipboard

        with pytest.raises(RuntimeError, match="No clipboard utility found"):
            copy_to_clipboard("hello")


# ── ProviderSetupModal helpers ───────────────────────────────────────────

class TestProviderSetupModal:
    def test_model_options_filters_agent_ready(self):
        from devx.tui.components.modals import ProviderSetupModal

        models = [
            ModelInfo("m1", free=True, tool_calling=True, coding=True),
            ModelInfo("m2", coding=True),  # not agent-ready (no tool calling)
            ModelInfo("m3", tool_calling=True, coding=True),
        ]
        assert ProviderSetupModal._model_options(models) == [
            ("m1 [free]", "m1"),
            ("m3", "m3"),
        ]

    def test_model_options_empty(self):
        from devx.tui.components.modals import ProviderSetupModal

        assert ProviderSetupModal._model_options([]) == []

    def test_save_writes_through_apply_init(self, tmp_path, monkeypatch):
        """The /setup modal must share the CLI ``devx init`` write path."""
        from devx.tui.components.modals import ProviderSetupModal

        captured: dict = {}

        def fake_apply_init(workspace, *, provider, model, api_key="", base_url="", embeddings=False, project=None):
            captured.update(
                workspace=workspace,
                provider=provider,
                model=model,
                api_key=api_key,
                base_url=base_url,
            )
            return {}

        monkeypatch.setattr("devx.configuration.apply_init", fake_apply_init)

        class _Value:
            def __init__(self, value):
                self.value = value

        class _Label:
            def __init__(self):
                self.text = ""

            def update(self, text):
                self.text = text

        class _Button:
            def __init__(self, id):
                self.id = id

        settings = Settings(workspace=tmp_path, provider="openrouter", model="openrouter/free")
        modal = ProviderSetupModal(settings, lambda: None, first_time=False)
        widgets = {
            "#setup-provider": _Value("openrouter"),
            "#setup-api-key": _Value("sk-test-key"),
            "#setup-base-url": _Value("https://openrouter.ai/api/v1"),
            "#setup-model": _Value("openrouter/free"),
            "#setup-error": _Label(),
        }
        modal.query_one = lambda selector, *a, **k: widgets[selector]  # type: ignore[method-assign]
        calls = {"done": 0, "dismiss": 0}
        modal.done = lambda: calls.__setitem__("done", calls["done"] + 1)  # type: ignore[method-assign]
        modal.dismiss = lambda: calls.__setitem__("dismiss", calls["dismiss"] + 1)  # type: ignore[method-assign]

        modal.on_button_pressed(SimpleNamespace(button=_Button("setup-save")))

        assert captured == {
            "workspace": tmp_path,
            "provider": "openrouter",
            "model": "openrouter/free",
            "api_key": "sk-test-key",
            "base_url": "https://openrouter.ai/api/v1",
        }
        assert calls == {"done": 1, "dismiss": 1}

    def test_exit_button_quits_via_action_quit(self, tmp_path, monkeypatch):
        """The Exit button must run app cleanup (``action_quit``), not bare exit."""
        from devx.tui.components.modals import ProviderSetupModal

        class _Button:
            def __init__(self, id):
                self.id = id

        class _App:
            def __init__(self):
                self.quit_calls = 0
                self.exit_calls = 0

            def action_quit(self):
                self.quit_calls += 1

            def exit(self):
                self.exit_calls += 1

        settings = Settings(workspace=tmp_path, provider="openrouter", model="openrouter/free")
        modal = ProviderSetupModal(settings, lambda: None, first_time=False)
        app = _App()
        # ``app`` is a read-only Textual property; patch it on the class for this test.
        monkeypatch.setattr(type(modal), "app", property(lambda self: app), raising=False)
        modal.dismiss = lambda: None  # type: ignore[method-assign]

        modal.on_button_pressed(SimpleNamespace(button=_Button("setup-exit")))

        assert app.quit_calls == 1
        assert app.exit_calls == 0


# ── AgentRunner (TUI) ────────────────────────────────────────────────────

class TestTuiAgentRunner:
    def test_unknown_mentions_abort(self, monkeypatch, tmp_path):
        from devx.tui.components.runner import AgentRunner

        app = FakeApp()
        patch_settings(monkeypatch, fake_settings(tmp_path, session_db=tmp_path / "s.db"))
        asyncio.run(AgentRunner(app).ask("fix @missing.py", plan_only=True, plan_first=False))
        assert any("Unknown mentions: missing.py" in line for line in app._written)
        assert app._run_active is False

    def test_settings_error_reported(self, monkeypatch, tmp_path):
        from devx.tui.components.runner import AgentRunner

        app = FakeApp()

        def raise_oserror():
            raise OSError("bad db")

        monkeypatch.setattr(Settings, "load", staticmethod(raise_oserror))
        asyncio.run(AgentRunner(app).ask("hello", plan_only=True, plan_first=False))
        assert any("Agent error: bad db" in line for line in app._written)
        assert app._run_active is False