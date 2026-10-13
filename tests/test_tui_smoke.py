import asyncio

import pytest

from devx.tui.app import DevTUI, DevxTUI


@pytest.fixture(autouse=True)
def configured_provider(monkeypatch):
    monkeypatch.setenv("DEVX_PROVIDER", "openrouter")
    monkeypatch.setenv("DEVX_API_KEY", "test-key")


def test_textual_app_smoke():
    assert DevTUI is DevxTUI

    async def run() -> None:
        async with DevxTUI().run_test() as pilot:
            await pilot.press("h", "e", "l", "p", "enter")
            await pilot.pause()

    asyncio.run(run())


def test_textual_command_surface_smoke():
    async def run() -> None:
        async with DevxTUI(session_id="command-test").run_test() as pilot:
            for command in ("/help", "/session", "/agent planner", "/clear", "/cancel"):
                await pilot.press(*list(command))
                await pilot.press("enter")
                await pilot.pause()

    asyncio.run(run())


def test_ctrl_q_is_bound_to_quit():
    # The user-guide documents Ctrl+Q as "Quit"; it must exist and route
    # through our ``action_quit`` override so remote backends are closed.
    assert ("ctrl+q", "quit", "Quit") in DevxTUI.BINDINGS
