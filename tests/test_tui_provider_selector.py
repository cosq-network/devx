"""Tests for the ``ProviderSelectionModal`` popup (Ctrl+P / ``/provider select``).

Covers the requirements for the screen-centered provider/model selector:

* provider list restricted to openai / openrouter / anthropic / ollama;
* dynamic model list driven by the selected provider;
* live search/filter over the model catalogue;
* configuration preview (base URL + API key status);
* Select / Cancel / Auto-detect actions;
* the Select action writes through ``apply_init`` - the same path as ``devx init``;
* Escape and Ctrl+P close the popup without touching configuration.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from devx.config import Settings
from devx.configuration import PROVIDERS, ModelInfo
from devx.tui.components.modals import ProviderSelectionModal

SUPPORTED_PROVIDERS = {"openai", "openrouter", "anthropic", "ollama"}


# ── fakes ─────────────────────────────────────────────────────────────────────


class _FakeSelect:
    def __init__(self, value, id):
        self.value = value
        self.id = id
        self.disabled = False
        self.options: list[tuple[str, str]] = []
        self.values_set: list = []

    def set_options(self, options) -> None:
        self.options = list(options)

    def __setattr__(self, name, value):
        if name == "value" and hasattr(self, "values_set"):
            self.values_set.append(value)
        object.__setattr__(self, name, value)


class _FakeStatic:
    def __init__(self, id="provider-details"):
        self.id = id
        self.text = ""

    def update(self, text) -> None:
        self.text = text


class _FakeApp:
    def __init__(self):
        self.notifications: list[tuple[str, str]] = []

    def notify(self, message, severity="information", **kwargs):
        self.notifications.append((str(message), severity))


def _build_modal(monkeypatch, tmp_path, **settings_kwargs):
    """Build a modal wired to fake widgets plus a captured apply_init call."""
    captured: dict = {}

    def fake_apply_init(
        workspace, *, provider, model, api_key="", base_url="", embeddings=False, project=None, global_only=False
    ):
        captured["args"] = dict(
            workspace=workspace,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            embeddings=embeddings,
        )
        return {}

    monkeypatch.setattr("devx.configuration.apply_init", fake_apply_init)

    settings = Settings(workspace=tmp_path, **settings_kwargs)
    modal = ProviderSelectionModal(settings, None)

    widgets = {
        "#provider-select": _FakeSelect(settings.provider, "provider-select"),
        "#model-select": _FakeSelect(settings.model, "model-select"),
        "#provider-details": _FakeStatic(),
    }
    modal.query_one = lambda selector, *a, **k: widgets[selector]  # type: ignore[method-assign]
    return modal, widgets, captured


def _press(button_id):
    return SimpleNamespace(button=SimpleNamespace(id=button_id))


def _press_button(modal, button_id) -> None:
    """Drive ``on_button_pressed`` - it is async (auto-detect awaits network)."""
    asyncio.run(modal.on_button_pressed(_press(button_id)))


# ── static behaviour ──────────────────────────────────────────────────────────


def test_provider_catalogue_is_restricted_to_supported_providers() -> None:
    assert set(PROVIDERS) == SUPPORTED_PROVIDERS


def test_model_options_flags_free_models_and_skips_non_agent_ready() -> None:
    models = [
        ModelInfo("m1", free=True, tool_calling=True, coding=True),
        ModelInfo("m2", coding=True),  # no tool calling -> excluded
        ModelInfo("m3", tool_calling=True, coding=True),
    ]
    assert ProviderSelectionModal._model_options(models) == [
        ("m1 [free]", "m1"),
        ("m3", "m3"),
    ]


def test_model_options_empty_list() -> None:
    assert ProviderSelectionModal._model_options([]) == []


def test_select_value_maps_blank_to_empty_string() -> None:
    from textual.widgets import Select

    assert ProviderSelectionModal._select_value(Select.BLANK) == ""
    assert ProviderSelectionModal._select_value(None) == "None"
    assert ProviderSelectionModal._select_value("  gpt-4o ") == "gpt-4o"


def test_ctrl_p_is_bound_on_the_tui() -> None:
    from devx.tui.app import DevTUI

    assert ("ctrl+p", "open_provider_selector", "Provider") in DevTUI.BINDINGS


def test_provider_select_command_routes_to_the_selector() -> None:
    """``/provider select`` must open the popup instead of listing providers."""
    from devx.tui.components.commands import ProviderManager

    opened: list[str] = []

    class _App:
        def _write(self, text):
            opened.append("write")

        def push_screen(self, screen):
            opened.append("push")

    manager = ProviderManager(_App())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "devx.tui.components.modals.ProviderSelectionModal",
            lambda *a, **k: object(),
        )
        manager.open_selector()
    assert opened == ["push"]


# ── selection flow ────────────────────────────────────────────────────────────


def test_select_button_writes_through_apply_init(tmp_path, monkeypatch):
    modal, widgets, captured = _build_modal(
        monkeypatch, tmp_path, provider="openrouter", model="openrouter/free", api_key="sk-live"
    )
    done: list[tuple[str, str]] = []
    modal.done = lambda p, m: done.append((p, m))  # type: ignore[method-assign]
    modal.dismiss = lambda result=None: None  # type: ignore[method-assign]
    app = _FakeApp()
    monkeypatch.setattr(type(modal), "app", property(lambda self: app), raising=False)

    _press_button(modal, "select-btn")

    assert captured["args"] == {
        "workspace": tmp_path,
        "provider": "openrouter",
        "model": "openrouter/free",
        "api_key": "sk-live",
        "base_url": PROVIDERS["openrouter"]["base_url"],
        "embeddings": False,
    }
    assert done == [("openrouter", "openrouter/free")]
    assert any("Selected: openrouter / openrouter/free" in msg for msg, _ in app.notifications)


def test_select_button_never_persists_the_placeholder_key(tmp_path, monkeypatch):
    modal, widgets, captured = _build_modal(
        monkeypatch, tmp_path, provider="openai", model="gpt-4o", api_key="sk-placeholder"
    )
    modal.dismiss = lambda result=None: None  # type: ignore[method-assign]
    app = _FakeApp()
    monkeypatch.setattr(type(modal), "app", property(lambda self: app), raising=False)

    _press_button(modal, "select-btn")

    assert captured["args"]["api_key"] == ""


def test_select_button_keeps_embeddings_flag(tmp_path, monkeypatch):
    modal, widgets, captured = _build_modal(
        monkeypatch, tmp_path, provider="ollama", model="llama3.1:8b", embeddings_enabled=True
    )
    modal.dismiss = lambda result=None: None  # type: ignore[method-assign]
    app = _FakeApp()
    monkeypatch.setattr(type(modal), "app", property(lambda self: app), raising=False)

    _press_button(modal, "select-btn")

    assert captured["args"]["embeddings"] is True
    assert captured["args"]["base_url"] == PROVIDERS["ollama"]["base_url"]


def test_select_button_with_blank_model_does_not_write(tmp_path, monkeypatch):
    from textual.widgets import Select

    modal, widgets, captured = _build_modal(monkeypatch, tmp_path, provider="openai", model="gpt-4o")
    widgets["#model-select"].value = Select.BLANK
    app = _FakeApp()
    monkeypatch.setattr(type(modal), "app", property(lambda self: app), raising=False)

    _press_button(modal, "select-btn")

    assert captured == {}
    assert app.notifications


def test_cancel_button_dismisses_without_writing(tmp_path, monkeypatch):
    modal, widgets, captured = _build_modal(monkeypatch, tmp_path, provider="openai", model="gpt-4o")
    dismissed: list = []
    modal.dismiss = lambda result=None: dismissed.append(result)  # type: ignore[method-assign]

    _press_button(modal, "cancel-btn")

    assert captured == {}
    assert dismissed == [None]


def test_escape_dismisses_without_writing(tmp_path, monkeypatch):
    modal, widgets, captured = _build_modal(monkeypatch, tmp_path, provider="openai", model="gpt-4o")
    dismissed: list = []
    modal.dismiss = lambda result=None: dismissed.append(result)  # type: ignore[method-assign]

    modal.on_key(SimpleNamespace(key="escape", stop=lambda: None))

    assert captured == {}
    assert dismissed == [None]


def test_apply_init_failure_keeps_the_popup_open(tmp_path, monkeypatch):
    from devx.configuration import ConfigurationError

    def boom(*a, **k):
        raise ConfigurationError("nope")

    monkeypatch.setattr("devx.configuration.apply_init", boom)
    settings = Settings(workspace=tmp_path, provider="openai", model="gpt-4o")
    modal = ProviderSelectionModal(settings, None)
    widgets = {
        "#provider-select": _FakeSelect("openai", "provider-select"),
        "#model-select": _FakeSelect("gpt-4o", "model-select"),
        "#provider-details": _FakeStatic(),
    }
    modal.query_one = lambda selector, *a, **k: widgets[selector]  # type: ignore[method-assign]
    dismissed: list = []
    modal.dismiss = lambda result=None: dismissed.append(result)  # type: ignore[method-assign]
    app = _FakeApp()
    monkeypatch.setattr(type(modal), "app", property(lambda self: app), raising=False)

    _press_button(modal, "select-btn")

    assert dismissed == []
    assert any(sev == "error" for _, sev in app.notifications)


# ── dynamic model list + search ──────────────────────────────────────────────


def test_changing_provider_refreshes_the_model_list(tmp_path, monkeypatch):
    modal, widgets, captured = _build_modal(monkeypatch, tmp_path, provider="openai", model="gpt-4o")
    widgets["#provider-select"].value = "ollama"

    modal.on_select_changed(SimpleNamespace(select=SimpleNamespace(id="provider-select")))

    labels = [label for label, _ in widgets["#model-select"].options]
    assert widgets["#model-select"].value == "qwen2.5-coder:7b"
    assert all("qwen" in label or "llama" in label for label in labels)


def test_search_filters_the_model_list(tmp_path, monkeypatch):
    modal, widgets, captured = _build_modal(monkeypatch, tmp_path, provider="openai", model="gpt-4o")
    modal._search_query = "gpt-4o"

    modal._refresh_model_list("gpt-4o")

    values = [value for _, value in widgets["#model-select"].options]
    assert values == ["gpt-4o", "gpt-4o-mini"]
    assert widgets["#model-select"].value == "gpt-4o"


def test_search_input_updates_the_query(tmp_path, monkeypatch):
    modal, widgets, captured = _build_modal(monkeypatch, tmp_path, provider="openai", model="gpt-4o")
    event = SimpleNamespace(
        input=SimpleNamespace(id="provider-search"),
        value="o3",
    )

    modal.on_input_changed(event)

    values = [value for _, value in widgets["#model-select"].options]
    assert values == ["o3-mini"]
    assert modal._search_query == "o3"


def test_search_input_for_other_widgets_is_ignored(tmp_path, monkeypatch):
    modal, widgets, captured = _build_modal(monkeypatch, tmp_path, provider="openai", model="gpt-4o")
    before = [value for _, value in widgets["#model-select"].options]

    modal.on_input_changed(SimpleNamespace(input=SimpleNamespace(id="other"), value="zzz"))

    assert [value for _, value in widgets["#model-select"].options] == before


# ── configuration preview ────────────────────────────────────────────────────


def test_details_show_base_url_and_key_status(tmp_path, monkeypatch):
    modal, widgets, captured = _build_modal(
        monkeypatch, tmp_path, provider="openai", model="gpt-4o", api_key="sk-live"
    )

    modal._update_details("openai")

    text = widgets["#provider-details"].text
    assert "Provider: openai" in text
    assert "Base URL: https://api.openai.com/v1" in text
    assert "API key: configured" in text
    assert "Coding models available: 5" in text


def test_details_flag_missing_api_key(tmp_path, monkeypatch):
    modal, widgets, captured = _build_modal(monkeypatch, tmp_path, provider="openai", model="gpt-4o")

    modal._update_details("openai")

    assert "API key: missing" in widgets["#provider-details"].text


def test_details_mark_ollama_key_as_not_required(tmp_path, monkeypatch):
    modal, widgets, captured = _build_modal(monkeypatch, tmp_path, provider="ollama", model="llama3.1:8b")

    modal._update_details("ollama")

    assert "API key: not required (local model)" in widgets["#provider-details"].text


def test_details_fall_back_to_settings_base_url(tmp_path, monkeypatch):
    modal, widgets, captured = _build_modal(monkeypatch, tmp_path, provider="openai", model="gpt-4o")

    modal._update_details("unknown-provider")

    assert f"Base URL: {modal.settings.base_url}" in widgets["#provider-details"].text


# ── live integration (real Textual widgets) ──────────────────────────────────


class _SelectorHostApp:
    """Minimal Textual app that mounts the popup so the real widgets run."""

    def __init__(self, settings):
        self.settings = settings

    def build(self):
        from textual.app import App
        from devx.tui.components.modals import ProviderSelectionModal

        settings = self.settings

        class SelectorHost(App):
            CSS = "ProviderSelectionModal { align: center middle; }"

            def on_mount(self) -> None:
                self.push_screen(ProviderSelectionModal(settings))

        return SelectorHost()


def test_modal_composes_and_mounts_without_errors(tmp_path, monkeypatch):
    """Regression: the first implementation crashed on ``catalog_models``."""
    import textual  # noqa: F401

    monkeypatch.setenv("DEVX_PROVIDER", "openrouter")
    monkeypatch.setenv("DEVX_MODEL", "openrouter/free")
    monkeypatch.setenv("DEVX_API_KEY", "test-key")
    app = _SelectorHostApp(Settings.load(workspace=tmp_path)).build()

    async def run():
        async with app.run_test() as pilot:
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, ProviderSelectionModal)
            assert modal.query_one("#provider-select").value == "openrouter"
            assert modal.query_one("#model-select").value == "openrouter/free"
            assert modal.query_one("#provider-search").value == ""
            return modal

    modal = asyncio.run(run())
    assert modal is not None


def test_modal_escape_closes_the_popup(tmp_path, monkeypatch):
    import textual  # noqa: F401

    monkeypatch.setenv("DEVX_PROVIDER", "openai")
    monkeypatch.setenv("DEVX_API_KEY", "test-key")
    app = _SelectorHostApp(Settings.load(workspace=tmp_path)).build()

    async def run():
        async with app.run_test() as pilot:
            await pilot.pause()
            assert type(app.screen).__name__ == "ProviderSelectionModal"
            await pilot.press("escape")
            await pilot.pause()
            return type(app.screen).__name__

    assert asyncio.run(run()) != "ProviderSelectionModal"


def test_modal_search_input_filters_models(tmp_path, monkeypatch):
    import textual  # noqa: F401

    from textual.widgets import Input, Select

    monkeypatch.setenv("DEVX_PROVIDER", "openai")
    monkeypatch.setenv("DEVX_API_KEY", "test-key")
    app = _SelectorHostApp(Settings.load(workspace=tmp_path)).build()

    async def run():
        async with app.run_test() as pilot:
            await pilot.pause()
            modal = app.screen
            modal.query_one("#provider-search", Input).value = "gpt-4o"
            await pilot.pause()
            values = [
                value
                for _, value in modal.query_one("#model-select", Select)._options
                if value is not Select.NULL
            ]
            return values, modal.query_one("#model-select", Select).value

    values, current = asyncio.run(run())
    assert values == ["gpt-4o", "gpt-4o-mini"]
    assert current == "gpt-4o"


def test_modal_tab_moves_between_fields(tmp_path, monkeypatch):
    import textual  # noqa: F401

    monkeypatch.setenv("DEVX_PROVIDER", "openai")
    monkeypatch.setenv("DEVX_API_KEY", "test-key")
    app = _SelectorHostApp(Settings.load(workspace=tmp_path)).build()

    async def run():
        async with app.run_test() as pilot:
            await pilot.pause()
            modal = app.screen
            focused_before = modal.focused.id if modal.focused else None
            await pilot.press("tab")
            await pilot.pause()
            focused_after = modal.focused.id if modal.focused else None
            return focused_before, focused_after

    before, after = asyncio.run(run())
    assert before == "provider-search"
    assert after != before


def test_modal_select_persists_the_choice(tmp_path, monkeypatch):
    import textual  # noqa: F401

    from textual.widgets import Select

    monkeypatch.setenv("DEVX_PROVIDER", "openai")
    monkeypatch.setenv("DEVX_API_KEY", "test-key")
    app = _SelectorHostApp(Settings.load(workspace=tmp_path)).build()

    async def run():
        async with app.run_test() as pilot:
            await pilot.pause()
            modal = app.screen
            modal.query_one("#model-select", Select).value = "o3-mini"
            modal.query_one("#select-btn").press()
            await pilot.pause()
            return (tmp_path / ".devx" / "config.json").exists(), (tmp_path / ".env").exists()

    config_written, env_written = asyncio.run(run())
    assert config_written is True
    assert env_written is True
    import json

    # ``apply_init`` stores provider/model credentials in ``.env`` (shared with
    # ``devx init``) and only project settings in ``.devx/config.json``.
    config = json.loads((tmp_path / ".devx" / "config.json").read_text(encoding="utf-8"))
    assert "context_compaction" in config
    env = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
        if "=" in line
    }
    assert env["DEVX_PROVIDER"] == "openai"
    assert env["DEVX_MODEL"] == "o3-mini"
    assert env["DEVX_BASE_URL"] == PROVIDERS["openai"]["base_url"]


# ── wiring into DevTUI ────────────────────────────────────────────────────────


def test_tui_action_opens_the_selector_via_provider_manager(monkeypatch, tmp_path):
    """Ctrl+P routes through ``ProviderManager.open_selector`` (single path)."""
    from devx.tui.app import DevTUI

    calls: list[str] = []

    class _Providers:
        def open_selector(self):
            calls.append("open")

    monkeypatch.chdir(tmp_path)
    app = DevTUI()
    app.providers = _Providers()

    app.action_open_provider_selector()

    assert calls == ["open"]


def test_auto_detect_uses_live_provider_api(tmp_path, monkeypatch):
    """Auto-detect discovers models from the provider endpoint, not the cache."""
    from devx.tui.components.modals import ProviderSelectionModal
    from devx.config import Settings

    seen: dict = {}

    def fake_list_models(base_url, api_key=""):
        seen["base_url"] = base_url
        seen["api_key"] = api_key
        return [ModelInfo("live-1", tool_calling=True, coding=True)]

    monkeypatch.setattr("devx.configuration.list_models", fake_list_models)

    modal = ProviderSelectionModal(
        Settings(workspace=tmp_path, provider="ollama", model="llama3.1:8b"),
        None,
    )
    widgets = {
        "#provider-select": _FakeSelect("ollama", "provider-select"),
        "#model-select": _FakeSelect("llama3.1:8b", "model-select"),
        "#provider-details": _FakeStatic(),
    }
    modal.query_one = lambda selector, *a, **k: widgets[selector]  # type: ignore[method-assign]
    app = _FakeApp()
    monkeypatch.setattr(type(modal), "app", property(lambda self: app), raising=False)

    asyncio.run(modal._auto_detect_models())

    assert seen["base_url"] == PROVIDERS["ollama"]["base_url"]
    assert [value for _, value in widgets["#model-select"].options] == ["live-1"]
    assert widgets["#model-select"].value == "live-1"
    assert widgets["#model-select"].disabled is False


def test_auto_detect_surfaces_failure_and_reenables_the_widget(tmp_path, monkeypatch):
    from devx.configuration import ConfigurationError
    from devx.tui.components.modals import ProviderSelectionModal
    from devx.config import Settings

    def boom(base_url, api_key=""):
        raise ConfigurationError("endpoint unreachable")

    monkeypatch.setattr("devx.configuration.list_models", boom)

    modal = ProviderSelectionModal(
        Settings(workspace=tmp_path, provider="openai", model="gpt-4o"), None
    )
    widgets = {
        "#provider-select": _FakeSelect("openai", "provider-select"),
        "#model-select": _FakeSelect("gpt-4o", "model-select"),
        "#provider-details": _FakeStatic(),
    }
    modal.query_one = lambda selector, *a, **k: widgets[selector]  # type: ignore[method-assign]
    app = _FakeApp()
    monkeypatch.setattr(type(modal), "app", property(lambda self: app), raising=False)

    asyncio.run(modal._auto_detect_models())

    assert widgets["#model-select"].disabled is False
    assert any(sev in {"warning", "error"} for _, sev in app.notifications)


def test_auto_detect_requires_a_provider(tmp_path, monkeypatch):
    from textual.widgets import Select
    from devx.tui.components.modals import ProviderSelectionModal
    from devx.config import Settings

    modal = ProviderSelectionModal(
        Settings(workspace=tmp_path, provider="openai", model="gpt-4o"), None
    )
    widgets = {
        "#provider-select": _FakeSelect(Select.BLANK, "provider-select"),
        "#model-select": _FakeSelect("gpt-4o", "model-select"),
        "#provider-details": _FakeStatic(),
    }
    modal.query_one = lambda selector, *a, **k: widgets[selector]  # type: ignore[method-assign]
    app = _FakeApp()
    monkeypatch.setattr(type(modal), "app", property(lambda self: app), raising=False)

    asyncio.run(modal._auto_detect_models())

    assert any("provider" in msg.lower() for msg, _ in app.notifications)
