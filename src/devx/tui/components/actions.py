from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

try:
    from textual.widgets import RichLog, Static
except ImportError:
    class RichLog:
        pass
    class Static:
        pass

if TYPE_CHECKING:
    from devx.tui.app import DevTUI


class ActionHandlers:
    def __init__(self, app: DevTUI):
        self.app = app

    def show_diff(self) -> None:
        from devx.config import Settings
        from devx.harness.permissions import PermissionPolicy
        from devx.harness.tools import WorkspaceTools

        settings = Settings.load()
        result = WorkspaceTools(
            PermissionPolicy(
                settings.workspace,
                settings.approval_required,
                getattr(settings, "permission_rules", ()),
            ),
            max_bytes=settings.max_file_bytes,
            timeout=settings.command_timeout,
        ).git_diff()
        self.app._write(result.output or "No uncommitted changes.")

    def undo_last_run(self) -> None:
        from devx.config import Settings
        from devx.harness.changes import ChangeJournal
        from devx.harness.session import SessionStore

        settings = Settings.load()
        try:
            session = SessionStore(settings.session_db).load(self.app.session_id)
        except (OSError, sqlite3.Error) as exc:
            self.app._write(f"Unable to load session: {exc}")
            return
        run_id = (session or {}).get("metadata", {}).get("last_run_id")
        if not run_id:
            self.app._write("No completed run is available to undo.")
            return
        try:
            restored = ChangeJournal(settings.session_db).rollback(run_id)
        except (OSError, sqlite3.Error) as exc:
            self.app._write(f"Unable to undo changes: {exc}")
            return
        self.app._write("Restored: " + ", ".join(restored) if restored else "No safe changes to restore.")

    def cancel_run(self) -> None:
        if self.app._cancellation:
            self.app._cancellation.cancel()
            self.app._write("Cancellation requested.")
        else:
            self.app._write("No active run to cancel.")

    def clear_chat(self) -> None:
        chat = self.app.query_one("#chat-view", RichLog)
        chat.clear()
        self.app._transcript.clear()
        self.app._write("Chat cleared. Ready for your next task.")

    def copy_transcript(self) -> None:
        from devx.tui.components.modals import copy_to_clipboard
        # RichLog doesn't have selected_text, use transcript
        content = "\n".join(self.app._transcript)
        try:
            copy_to_clipboard(content)
        except RuntimeError as exc:
            self.app.query_one("#status-text", Static).update(f"● copy failed: {exc}")
        else:
            self.app.query_one("#status-text", Static).update("● copied transcript to clipboard")

    def copy_or_cancel(self) -> None:
        # RichLog doesn't have selected_text, use transcript
        if self.app._transcript:
            self.copy_transcript()
        else:
            self.cancel_run()

    def open_setup(self) -> None:
        from devx.config import Settings
        self.app.push_screen(
            self.app.ProviderSetupModal(Settings.load(), self.app._finish_reconfiguration, first_time=False)
        )

    def show_config(self) -> None:
        from devx.config import Settings
        from devx.configuration import mask_secret
        settings = Settings.load()
        self.app._write(
            f"provider: {settings.provider}\nbase_url: {settings.base_url}\nmodel: {settings.model}\napi_key: {mask_secret(settings.api_key)}\nworkspace: {settings.workspace}"
        )

    def reload_config(self) -> None:
        from devx.config import Settings
        settings = Settings.load()
        self.app._workspace = settings.workspace
        self.app._write(f"Configuration reloaded for future runs: {settings.provider} / {settings.model}")

    def set_api_key(self) -> None:
        from devx.configuration import ConfigurationError, UserConfig
        from devx.tui.components.modals import SecretInputModal

        def save_key(api_key: str) -> None:
            try:
                UserConfig().set_api_key(api_key)
                self.app._write("API key saved securely to the user configuration file.")
            except ConfigurationError as exc:
                self.app._write(str(exc))
        self.app.push_screen(SecretInputModal(save_key))

    def set_agent(self, requested: str) -> None:
        requested = requested.strip() or "supervisor"
        if requested not in {"supervisor", "planner", "coder", "tester"}:
            self.app._write("Choose: supervisor, planner, coder, or tester")
        else:
            self.app._requested_agent = requested
            self.app.query_one("#status-text", Static).update(f"● agent: {requested}")

    def run_plan(self, task: str) -> None:
        if not task:
            self.app._write("Usage: /plan <task>")
        elif self.app._run_active:
            self.app._write("A run is already active. Use /cancel before starting another task.")
        else:
            self.app._write(f"> {task} (plan only)")
            self.app._run_active = True
            self.app.run_worker(self.app._ask(task, plan_only=True), exclusive=False)