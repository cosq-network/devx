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
        if getattr(self.app, "_remote_ssh", ""):
            object.__setattr__(settings, "remote_ssh", self.app._remote_ssh)
        backend = getattr(self.app, "_remote_backend", None)
        result = WorkspaceTools(
            PermissionPolicy.from_settings(settings),
            max_bytes=settings.max_file_bytes,
            timeout=settings.command_timeout,
            backend=backend,
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
            backend = getattr(self.app, "_remote_backend", None)
            restored = ChangeJournal(settings.session_db).rollback(run_id, backend=backend)
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
        from devx.tui.components.modals import ProviderSetupModal
        self.app.push_screen(
            ProviderSetupModal(Settings.load(), self.app._finish_reconfiguration, first_time=False)
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
        try:
            settings = Settings.load(workspace=getattr(self.app, "_workspace", None))
        except TypeError:
            settings = Settings.load()
        self.app._workspace = settings.workspace
        self.app.update_model_status()
        self.app._write(f"Configuration reloaded for future runs: {settings.provider} / {settings.model}")

    def reset_config(self) -> None:
        import os
        from pathlib import Path
        from devx.config import Settings
        from devx.configuration import UserConfig

        # 1. Reset user configuration ~/.devx/config.env
        UserConfig().reset()

        # 2. Reset legacy user configuration ~/.dev/config.env if present
        legacy_path = Path.home() / ".dev" / "config.env"
        if legacy_path.is_file():
            UserConfig(legacy_path).reset()

        # 3. Reset workspace .env if present
        workspace = getattr(self.app, "_workspace", Path.cwd())
        workspace_env = workspace / ".env"
        if workspace_env.is_file():
            UserConfig(workspace_env).reset()

        # 4. Remove process-level overrides
        for key in (
            "DEVX_PROVIDER", "DEV_PROVIDER",
            "DEVX_MODEL", "DEV_MODEL",
            "DEVX_BASE_URL", "DEV_BASE_URL",
            "DEVX_API_KEY", "DEV_API_KEY",
        ):
            os.environ.pop(key, None)

        # 5. Reload settings and update status bar
        try:
            settings = Settings.load(workspace=workspace)
        except TypeError:
            settings = Settings.load()
        self.app._workspace = settings.workspace
        self.app.update_model_status()
        self.app._write(
            f"Configuration reset to defaults: {settings.provider} / {settings.model}\n"
            "Use /setup or /provider to configure your provider and model."
        )

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
            self.app._set_running(True)
            self.app.run_worker(self.app.runner.ask(task, plan_only=True), exclusive=False)

    def connect_remote(self, uri: str) -> None:
        from devx.config import Settings
        uri = uri.strip()
        if not uri:
            self.app._write("Usage: /remote connect <ssh-uri> (e.g. ssh://user@host:port/path or user@host:/path)")
            return
        try:
            from devx.harness.ssh_backend import SshConfig, SshWorkspaceBackend
            cfg = SshConfig.from_uri(uri)
            settings = Settings.load()
            if getattr(settings, "ssh_key_path", ""):
                cfg = SshConfig(
                    host=cfg.host,
                    username=cfg.username,
                    port=cfg.port,
                    password=cfg.password,
                    key_filename=settings.ssh_key_path,
                    remote_path=cfg.remote_path,
                    proxy_command=cfg.proxy_command,
                )
            if getattr(self.app, "_remote_backend", None) is not None:
                try:
                    self.app._remote_backend.close()
                except Exception:
                    pass
            backend = SshWorkspaceBackend(cfg)
            self.app._remote_backend = backend
            self.app._remote_ssh = uri
            object.__setattr__(settings, "remote_ssh", uri)
            self.app.query_one("#status-text", Static).update(f"● remote: {cfg.host}:{cfg.remote_path}")
            self.app._write(f"Connected remote SSH workspace: {cfg.username}@{cfg.host}:{cfg.remote_path}")
        except Exception as exc:
            self.app._write(f"Remote connection error: {exc}")

    def disconnect_remote(self) -> None:
        from devx.config import Settings
        if getattr(self.app, "_remote_backend", None) is not None:
            try:
                self.app._remote_backend.close()
            except Exception:
                pass
            self.app._remote_backend = None
        self.app._remote_ssh = ""
        settings = Settings.load()
        object.__setattr__(settings, "remote_ssh", "")
        self.app.query_one("#status-text", Static).update("● ready")
        self.app._write("Disconnected remote workspace. Reverted to local workspace.")