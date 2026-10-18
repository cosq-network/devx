from __future__ import annotations

import sqlite3
import time
from dataclasses import replace
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

    def cancel_run(self) -> str:
        """Request cancellation of the active run; returns the chat message."""
        if self.app._cancellation:
            self.app._cancellation.cancel()
            message = "Cancellation requested."
            self.app._write(message)
        else:
            message = "No active run to cancel."
            self.app._write(message)
        return message

    def clear_chat(self) -> None:
        chat = self.app.query_one("#chat-view", RichLog)
        chat.clear()
        self.app._transcript.clear()
        self.app._write("Chat cleared. Ready for your next task.")

    def copy_transcript(self) -> str:
        from devx.tui.components.modals import copy_to_clipboard
        # RichLog doesn't have selected_text, use transcript
        content = "\n".join(self.app._transcript)
        try:
            label = copy_to_clipboard(content, app=self.app)
        except RuntimeError as exc:
            message = f"● copy failed: {exc}"
        else:
            label = label or ""
            suffix = f" ({label})" if label else ""
            message = f"● copied transcript to clipboard{suffix}"
        self.app.query_one("#status-text", Static).update(message)
        return message

    def copy_or_cancel(self) -> None:
        """Handle Ctrl+C: cancel the active run, otherwise copy the transcript.

        A second Ctrl+C within ``DOUBLE_CTRL_C_WINDOW`` (default 1.5 s) quits
        the app entirely, so pressing Ctrl+C twice always lets the user exit.
        """
        now = time.monotonic()
        app = self.app
        window = float(getattr(app, "DOUBLE_CTRL_C_WINDOW", 1.5))
        if getattr(app, "_ctrl_c_armed", False) and (now - getattr(app, "_ctrl_c_at", 0.0)) <= window:
            app._ctrl_c_armed = False
            app.action_quit()
            return
        app._ctrl_c_at = now
        app._ctrl_c_armed = True
        if getattr(app, "_run_active", False):
            message = self.cancel_run()
        else:
            message = self.copy_transcript()
        self._set_status(f"{message} — press Ctrl+C again to quit")
        self._schedule_disarm(window)

    def _set_status(self, message: str) -> None:
        try:
            self.app.query_one("#status-text", Static).update(message)
        except Exception:
            pass

    def _schedule_disarm(self, window: float) -> None:
        """Disarm the double-press window once it expires (real apps only)."""
        try:
            set_timer = getattr(self.app, "set_timer", None)
            if callable(set_timer):
                set_timer(window, self._disarm_ctrl_c)
        except Exception:
            pass

    def _disarm_ctrl_c(self) -> None:
        try:
            self.app._ctrl_c_armed = False
        except Exception:
            pass

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

    def set_mode(self, requested: str) -> None:
        from devx.config import Settings
        requested = requested.strip().lower()
        valid_modes = {"architect", "code", "review", "ask"}
        if not requested:
            current = getattr(self.app, "_mode", "code")
            self.app._write(f"Current operational mode: {current}. Available: {', '.join(sorted(valid_modes))}")
            return
        if requested not in valid_modes:
            self.app._write(f"Invalid mode: {requested}. Choose one of: {', '.join(sorted(valid_modes))}")
            return
        self.app._mode = requested
        settings = Settings.load()
        object.__setattr__(settings, "mode", requested)
        self.app.query_one("#status-text", Static).update(f"● mode: {requested}")
        self.app._write(f"Switched operational mode to: {requested}")

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
                cfg = replace(cfg, key_filename=settings.ssh_key_path)
            cfg = replace(
                cfg,
                known_hosts=getattr(settings, "ssh_known_hosts", "") or None,
                allow_unknown_host_keys=getattr(settings, "ssh_allow_unknown_keys", False),
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

    def run_review(self, focus: str = "all") -> None:
        from devx.config import Settings
        from devx.harness.permissions import PermissionPolicy
        from devx.harness.tools import WorkspaceTools
        from devx.review.reviewer import CodeReviewer

        settings = Settings.load()
        backend = getattr(self.app, "_remote_backend", None)
        tools = WorkspaceTools(PermissionPolicy.from_settings(settings), backend=backend)
        diff_res = tools.git_diff()
        if not diff_res.ok or not diff_res.output.strip():
            self.app._write("No uncommitted git changes to review.")
            return

        self.app._write("🔍 Running multi-perspective code review...")
        rev = CodeReviewer(settings)
        report = rev.review_diff(diff_res.output, focus=focus)
        saved = rev.save_report(report, name="tui")
        self.app._write(f"{report.to_markdown()}\n*Report saved to {saved}*")

    def run_design(self, kind: str = "flowchart", focus: str = "all") -> None:
        from devx.config import Settings
        from devx.design.architect import ArchitectureEngine

        settings = Settings.load()
        engine = ArchitectureEngine(settings)
        rendered = engine.generate_diagram(kind=kind or "flowchart", focus=focus)
        self.app._write(f"```mermaid\n{rendered}\n```")

    def create_adr(self, title: str) -> None:
        from devx.config import Settings
        from devx.design.architect import ArchitectureEngine

        if not title:
            self.app._write("Usage: /adr <title>")
            return

        settings = Settings.load()
        engine = ArchitectureEngine(settings)
        record, path = engine.create_adr(
            title=title,
            context=f"Architecture context for {title}.",
            decision="Adopted proposed architectural decision.",
            consequences="Maintains clean boundaries and ensures testability.",
        )
        self.app._write(f"Created ADR-{record.number:04d}: `{path}`")

    def run_test(self, action: str = "impact", target: str = "") -> None:
        from devx.config import Settings
        from devx.test_engine.smart_runner import SmartTestRunner

        settings = Settings.load()
        runner = SmartTestRunner(settings)

        if action == "flaky":
            if not target:
                self.app._write("Usage: /test flaky <test_path>")
                return
            self.app._write(f"Probing {target} for flakiness (3 runs)...")
            rep = runner.check_flakiness(target, runs=3)
            self.app._write(f"Flakiness check: {rep.passes}/{rep.total_runs} passed ({rep.pass_rate:.1f}%). Flaky: {rep.is_flaky}.")
        else:
            impacted = runner.get_impacted_tests([target] if target else None)
            if not impacted:
                self.app._write("No impacted test files detected for recent changes.")
            else:
                self.app._write(f"Impacted test files ({len(impacted)}):\n" + "\n".join(f"- {t}" for t in impacted))