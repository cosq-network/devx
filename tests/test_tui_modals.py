"""Regression tests for the shared modal base classes in ``components.modals``.

These cover two bugs that made every popup in the TUI unusable:

* ``modals.py`` re-declared stub ``Select`` / ``ModalScreen`` classes at import
  time, shadowing the real Textual ones - so ``app.push_screen(...)`` raised
  ``TypeError: push_screen requires a Screen instance or str`` for every modal;
* every ``compose()`` built its widgets inside a ``with`` block but never
  ``yield``-ed them, so mounting a modal raised ``TypeError: 'NoneType' object
  is not iterable``.
"""

from __future__ import annotations

import asyncio

from textual.screen import ModalScreen, Screen

from devx.harness.approval import ApprovalDecision, ApprovalRequest
from devx.tui.components.modals import (
    ApprovalModal,
    PlanPreviewModal,
    ProviderSelectionModal,
    ProviderSetupModal,
    SecretInputModal,
)

ALL_MODALS = (
    ApprovalModal,
    SecretInputModal,
    ProviderSetupModal,
    ProviderSelectionModal,
    PlanPreviewModal,
)


class _Host:
    """Minimal Textual host that can mount a modal under the DevTUI stylesheet."""

    def build(self, screen_factory, app_cls):
        from textual.app import App

        class Host(App):
            CSS = app_cls.CSS if app_cls is not None else ""

            def on_mount(self) -> None:
                self.push_screen(screen_factory())

        return Host()


def test_modals_are_real_textual_screens() -> None:
    """Regression: stub classes used to shadow ``textual.screen.ModalScreen``."""
    from textual.widgets import Select

    import devx.tui.components.modals as modals

    assert modals.ModalScreen is ModalScreen
    assert modals.Select is Select
    for modal in ALL_MODALS:
        assert issubclass(modal, Screen)
        assert issubclass(modal, ModalScreen)
        # Must be a real Textual screen, i.e. registered in the screen base
        # classes that ``push_screen`` checks.
        assert modal.__name__ in {cls.__name__ for cls in modal.__mro__}


def test_compose_yields_widgets_for_every_modal(tmp_path) -> None:
    """Regression: ``compose()`` returned ``None`` and crashed on mount."""
    from textual.app import App

    from devx.config import Settings

    settings = Settings(workspace=tmp_path)
    approval = ApprovalRequest(
        run_id="run-1",
        action="shell",
        target="make deploy",
        reason="publish the build",
    )

    cases = [
        (ApprovalModal(approval, lambda d: None), "#approval-dialog"),
        (SecretInputModal(lambda k: None), "#secret-dialog"),
        (ProviderSetupModal(settings, lambda: None), "#setup-dialog"),
        (ProviderSelectionModal(settings), "#provider-selector"),
        (PlanPreviewModal("1. plan step", lambda ok: None), "#plan-preview-dialog"),
    ]

    async def run() -> list[tuple[str, int]]:
        results: list[tuple[str, int]] = []
        for modal, selector in cases:
            class Host(App):
                def on_mount(self) -> None:
                    self.push_screen(modal)

            app = Host()
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                await pilot.pause()
                results.append((type(modal).__name__, len(list(app.screen.query(selector)))))
        return results

    for name, count in asyncio.run(run()):
        assert count == 1, f"{name} did not compose its dialog container"


def test_approval_modal_dispatches_real_decision(tmp_path) -> None:
    """Regression: imported ``ApprovalDecision`` from ``textual.app``."""
    from devx.tui.app import DevTUI

    approval = ApprovalRequest(
        run_id="run-1",
        action="shell",
        target="git push",
        reason="share the branch",
    )
    seen: list[str] = []

    async def run() -> str:
        app = DevTUI()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.push_screen(ApprovalModal(approval, seen.append))
            await pilot.pause()
            await pilot.pause()
            app.screen.query_one("#deny").press()
            await pilot.pause()
            return type(app.screen).__name__

    screen_after = asyncio.run(run())
    assert seen == [ApprovalDecision.DENY]
    # The approval modal dismissed (whatever is underneath is the setup or
    # default screen, not the approval dialog).
    assert screen_after != "ApprovalModal"


def test_modals_render_centred_under_devtui_stylesheet(tmp_path) -> None:
    """The popup must be a centred panel, not a full-screen sheet."""
    from devx.config import Settings
    from devx.tui.app import DevTUI

    settings = Settings(workspace=tmp_path)

    async def run():
        app = DevTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(ProviderSelectionModal(settings))
            await pilot.pause()
            await pilot.pause()
            region = app.screen.query_one("#provider-selector").region
            focused = app.screen.focused.id if app.screen.focused else None
            return region, focused, app.size

    region, focused, size = asyncio.run(run())
    # Centred horizontally and vertically, and smaller than the full screen.
    assert region.x == (size.width - region.width) // 2
    assert region.y == (size.height - region.height) // 2
    assert region.width < size.width
    assert region.height < size.height
    assert focused == "provider-search"


def test_provider_selector_action_buttons_fit_the_dialog(tmp_path) -> None:
    from devx.config import Settings
    from devx.tui.app import DevTUI

    async def run():
        app = DevTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(ProviderSelectionModal(Settings(workspace=tmp_path)))
            await pilot.pause()
            await pilot.pause()
            dialog = app.screen.query_one("#provider-selector").region
            buttons = {
                name: app.screen.query_one(f"#{name}").region
                for name in ("select-btn", "auto-detect-btn", "cancel-btn")
            }
            return dialog, buttons

    dialog, buttons = asyncio.run(run())
    for name, region in buttons.items():
        assert region.width > 1, f"{name} collapsed"
        assert dialog.x <= region.x and region.x + region.width <= dialog.x + dialog.width, name
        assert dialog.y <= region.y and region.y + region.height <= dialog.y + dialog.height, name
