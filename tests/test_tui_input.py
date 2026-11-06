import asyncio
from pathlib import Path

from devx.config import Settings
from devx.tui.app import DevTUI
from devx.tui.components.input import PromptInput
from textual.app import App, ComposeResult
from textual.widgets import Button


def test_tab_accepts_slash_command_and_enter_submits(monkeypatch, tmp_path: Path):
    settings = Settings(workspace=tmp_path, api_key="test-key")
    monkeypatch.setattr("devx.config.Settings.load", staticmethod(lambda: settings))

    async def run() -> None:
        async with DevTUI().run_test() as pilot:
            await pilot.click("#input-bar")
            await pilot.press("/", "h")
            await pilot.pause()
            await pilot.press("tab")

            prompt = pilot.app.query_one("#input-bar", PromptInput)
            assert prompt.value == "/help"
            await pilot.press("enter")
            assert pilot.app._transcript[-1].startswith("QUICK START\n")

    asyncio.run(run())


def test_tab_accepts_file_and_directory_suggestions(monkeypatch, tmp_path: Path):
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "app" / "main.py").write_text("", encoding="utf-8")
    settings = Settings(workspace=tmp_path, api_key="test-key")
    monkeypatch.setattr("devx.config.Settings.load", staticmethod(lambda: settings))

    async def run() -> None:
        async with DevTUI().run_test() as pilot:
            prompt = pilot.app.query_one("#input-bar", PromptInput)
            await pilot.click("#input-bar")
            await pilot.press(*list("inspect @s"))
            await pilot.pause()
            await pilot.press("tab")
            assert prompt.value == "inspect @src/"

            prompt.value = ""
            await pilot.press(*list("inspect @src/app/"))
            await pilot.pause()
            await pilot.press("tab")
            assert prompt.value == "inspect @src/app/main.py"

    asyncio.run(run())


def test_right_arrow_still_accepts_suggestions(monkeypatch, tmp_path: Path):
    settings = Settings(workspace=tmp_path, api_key="test-key")
    monkeypatch.setattr("devx.config.Settings.load", staticmethod(lambda: settings))

    async def run() -> None:
        async with DevTUI().run_test() as pilot:
            await pilot.click("#input-bar")
            await pilot.press("/", "h")
            await pilot.pause()
            await pilot.press("right")
            assert pilot.app.query_one("#input-bar", PromptInput).value == "/help"

    asyncio.run(run())


def test_tab_without_suggestion_keeps_text_and_moves_focus():
    class FocusApp(App):
        def compose(self) -> ComposeResult:
            yield PromptInput(id="prompt")
            yield Button("Next", id="next")

    async def run() -> None:
        async with FocusApp().run_test() as pilot:
            await pilot.click("#prompt")
            await pilot.press("x", "tab")
            assert pilot.app.query_one("#prompt", PromptInput).value == "x"
            assert pilot.app.focused.id == "next"

    asyncio.run(run())

