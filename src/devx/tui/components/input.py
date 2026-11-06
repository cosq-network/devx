from __future__ import annotations

from typing import ClassVar

from textual.widgets import Input


class PromptInput(Input):
    """The TUI prompt input with completion-aware Tab navigation."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("tab", "accept_completion_or_focus_next", "Accept suggestion or focus next"),
    ]

    def action_accept_completion_or_focus_next(self) -> None:
        """Accept a visible completion, or preserve normal Tab navigation."""
        if self._suggestion:
            # Keep completion behavior identical to the built-in Right Arrow action.
            self.action_cursor_right()
            return

        self.app.action_focus_next()
