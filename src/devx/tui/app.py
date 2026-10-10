from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Footer, Header, Input, ProgressBar, RichLog, Static
else:
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal, Vertical
        from textual.widgets import Footer, Header, Input, ProgressBar, RichLog, Static
    except ImportError:  # pragma: no cover
        class App:
            pass
        
        class ComposeResult:
            pass
        
        class Horizontal:
            pass
        
        class Vertical:
            pass
        
        class Footer:
            pass
        
        class Header:
            pass
        
        class Input:
            pass
        
        class ProgressBar:
            pass
        
        class RichLog:
            pass
        
        class Static:
            pass

if TYPE_CHECKING:
    from devx.harness.runtime import CancellationToken

from devx.tui.components import (
    ProviderSetupModal,
    HelpSystem,
    ProviderManager,
    ModelManager,
    SessionManager,
    ActionHandlers,
    AgentRunner,
)


class DevTUI(App):
    CSS_PATH = None
    TITLE = "devx • coding agent"
    SUB_TITLE = "local-first • approval-gated"
    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("f1", "show_help", "Help"),
        ("ctrl+l", "clear_chat", "Clear"),
        ("ctrl+shift+c", "copy_transcript", "Copy"),
        ("ctrl+insert", "copy_transcript", "Copy"),
        ("f2", "copy_transcript", "Copy"),
        ("ctrl+c", "copy_or_cancel", "Copy/Cancel"),
    ]
    CSS = """
    $primary: #303030;
    $secondary: #4a4a4a;
    $accent: #bdbdbd;
    $success: #d0d0d0;
    $warning: #b0b0b0;
    $error: #bdbdbd;
    $surface: #111111;
    $panel: #1c1c1c;
    $text: #f2f2f2;
    $text-muted: #999999;
    $surface-lighten-1: #1b1b1b;
    $surface-lighten-2: #242424;

    Screen {
        background: #111111;
        width: 100%;
        height: 100%;
    }
    Screen > Vertical {
        width: 100%;
        height: 100%;
    }
    Header {
        background: #202020;
        color: #f2f2f2;
    }
    Footer {
        background: #1c1c1c;
        color: #bdbdbd;
    }
    ProviderSetupModal {
        align: center middle;
    }
    #welcome {
        height: auto;
        width: 100%;
        margin: 0;
        padding: 1 2 0 2;
        background: #111111;
    }
    #welcome-title {
        color: #e0e0e0;
        text-style: bold;
    }
    #welcome-subtitle {
        color: #999999;
        padding: 0 0 1 0;
    }
    #chat-view {
        height: 1fr;
        width: 100%;
        border: none;
        padding: 1 2;
        margin: 0;
        background: #111111;
        scrollbar-size: 1 1;
    }
    #log-view {
        height: 6;
        width: 100%;
        border: none;
        padding: 0 2;
        margin: 0;
        background: #0a0a0a;
        scrollbar-size: 1 1;
        color: #999999;
        text-style: dim;
    }
    #log-view:focus {
        border: none;
    }
    #input-bar {
        height: 3;
        width: 100%;
        margin: 0;
        border: none;
        background: #1b1b1b;
    }
    #status-bar {
        height: 1;
        width: 100%;
        margin: 0;
        padding: 0 1;
        background: #1c1c1c;
        color: #d0d0d0;
    }
    #status-bar .status-item {
        width: 1fr;
    }
    #status-text {
        color: #d0d0d0;
    }
    #progress-bar {
        width: 4;
        height: 1;
        padding: 0 1 0 0;
        background: $panel;
        color: $accent;
        text-style: bold;
        display: none;
    }
    #input-bar:focus {
        border: none;
        background: #242424;
    }
    Button {
        background: #303030;
        color: #f2f2f2;
        border: none;
    }
    Button:hover, Button:focus {
        background: #4a4a4a;
        color: #ffffff;
    }
    #setup-dialog {
        width: 72;
        max-width: 90%;
        height: auto;
        padding: 1 2;
        background: #151515;
        border: round #bdbdbd;
    }
    #setup-title {
        color: #e0e0e0;
        text-style: bold;
    }
    #setup-help, #setup-error {
        color: #999999;
        margin-bottom: 1;
    }
    #setup-error {
        color: #bdbdbd;
    }
    #setup-actions {
        width: 100%;
        height: auto;
        align: center middle;
    }
    #setup-actions Button {
        margin: 1 1;
        padding: 1 2;
    }
    #setup-save {
        background: #3a3a3a;
        color: #ffffff;
    }
    #setup-exit {
        background: #252525;
        color: #d0d0d0;
    }
    """

    def __init__(self, session_id: str | None = None):
        super().__init__()
        self.session_id = session_id or "default"
        self._remote_ssh = ""
        self._remote_backend = None
        try:
            from devx.config import Settings
            _loaded = Settings.load()
            self._workspace = _loaded.workspace
            self._remote_ssh = _loaded.remote_ssh or ""
        except (OSError, ValueError):
            self._workspace = Path.cwd()
        self._cancellation: CancellationToken | None = None
        self._requested_agent = "supervisor"
        self._transcript: list[str] = []
        self._run_active = False

        # Initialize component managers
        self.help = HelpSystem(self)
        self.providers = ProviderManager(self)
        self.models = ModelManager(self)
        self.sessions = SessionManager(self)
        self.actions = ActionHandlers(self)
        self.runner = AgentRunner(self)

    def action_quit(self) -> None:
        if getattr(self, "_remote_backend", None) is not None:
            try:
                self._remote_backend.close()
            except Exception:
                pass
            self._remote_backend = None
        self.exit()

    @property
    def _model_choices(self) -> list[str]:
        return self.models._model_choices

    @property
    def _provider_choices(self) -> list[str]:
        return self.providers._provider_choices

    @property
    def chat_text(self) -> str:
        """Return the current chat content as text (for test compatibility)."""
        return "\n".join(self._transcript)

    def compose(self) -> ComposeResult:
        from devx.completion.suggester import CommandMentionSuggester

        with Vertical():
            yield Header(show_clock=True)
            with Vertical(id="welcome"):
                yield Static("DEVX CODING AGENT", id="welcome-title")
                yield Static(
                    "Describe a task, reference files with @path, or use /help for commands.",
                    id="welcome-subtitle",
                )
            yield RichLog(
                id="chat-view",
                wrap=True,
                highlight=True,
                auto_scroll=True,
                markup=True,
            )
            yield RichLog(
                id="log-view",
                wrap=True,
                highlight=True,
                auto_scroll=True,
            )
            yield Input(
                placeholder="Enter a task... use /commands or @file mentions",
                suggester=CommandMentionSuggester(self._workspace),
                id="input-bar",
            )
            with Horizontal(id="status-bar"):
                yield ProgressBar(id="progress-bar", total=None, show_bar=True, show_percentage=False, show_eta=False)
                yield Static("● ready", id="status-text", classes="status-item")
                yield Static(f"session: {self.session_id}", id="session-text", classes="status-item")
                yield Static("hint: /help • /setup • /model", id="hint-text", classes="status-item")
            yield Footer()

    def on_mount(self) -> None:
        from devx.config import Settings

        settings = Settings.load()
        if self._needs_provider_setup(settings):
            self.push_screen(ProviderSetupModal(settings, self._finish_setup))
        else:
            self._finish_setup()

    @staticmethod
    def _needs_provider_setup(settings) -> bool:
        return settings.provider != "ollama" and settings.api_key in {"", "sk-placeholder"}

    def _finish_setup(self) -> None:
        self.query_one("#input-bar", Input).focus()
        self._write("Ready. Type a task or /help for commands.")

    def _finish_reconfiguration(self) -> None:
        from devx.config import Settings

        settings = Settings.load()
        self._write(f"Configuration saved: {settings.provider} / {settings.model}")
        self.query_one("#input-bar", Input).focus()

    def _log(self, message: str) -> None:
        try:
            log = self.query_one("#log-view", RichLog)
            log.write(message)
        except Exception:
            pass

    def _set_running(self, active: bool) -> None:
        progress = self.query_one("#progress-bar", ProgressBar)
        status = self.query_one("#status-text", Static)
        if active:
            progress.display = True
            progress.progress = 0
            status.update("◌ thinking...")
            self._progress_value = 0
            self._progress_direction = 1
            if not hasattr(self, "_progress_interval") or not self._progress_interval:
                self._progress_interval = self.set_interval(0.25, self._tick_progress)
        else:
            progress.display = False
            progress.progress = 0
            status.update("● ready")
            if hasattr(self, "_progress_interval") and self._progress_interval:
                self._progress_interval.stop()
                self._progress_interval = None

    def _tick_progress(self) -> None:
        try:
            progress = self.query_one("#progress-bar", ProgressBar)
            self._progress_value = max(0, min(100, self._progress_value + self._progress_direction * 8))
            if self._progress_value >= 100:
                self._progress_direction = -1
            elif self._progress_value <= 0:
                self._progress_direction = 1
            progress.progress = self._progress_value
        except Exception:
            pass

    def action_show_help(self, topic: str = "") -> None:
        self.help.show_help(topic)

    def action_clear_chat(self) -> None:
        self.actions.clear_chat()

    def action_copy_transcript(self) -> None:
        self.actions.copy_transcript()

    def action_copy_or_cancel(self) -> None:
        self.actions.copy_or_cancel()

    def _write(self, message: str) -> None:
        self._transcript.append(message)
        try:
            chat = self.query_one("#chat-view", RichLog)
            chat.write(message)
        except Exception:
            # Fallback for when RichLog is not available
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        event.input.value = ""
        if not value:
            return
        if value == "/exit":
            self.action_quit()
        elif value in {"/setup", "/configure"}:
            self.actions.open_setup()
        elif value == "/clear":
            self.action_clear_chat()
        elif value == "/help":
            self.action_show_help()
        elif value.startswith("/help "):
            self.action_show_help(value.partition(" ")[2])
        elif value == "/copy":
            self.action_copy_transcript()
        elif value == "/diff":
            self.actions.show_diff()
        elif value in {"/undo", "/rollback"}:
            self.actions.undo_last_run()
        elif value.startswith("/plan "):
            task = value.partition(" ")[2].strip()
            self.actions.run_plan(task)
        elif value == "/provider" or value == "/provider list":
            self.providers.show_providers()
        elif value.startswith("/provider use "):
            self.providers.select_provider(value.partition(" ")[2].partition(" ")[2])
        elif value.startswith("/provider "):
            self.providers.select_provider(value.partition(" ")[2])
        elif value == "/model" or value in {"/model catalog", "/model list"}:
            self.models.show_catalog()
        elif value.startswith("/model catalog "):
            self.models.show_catalog(value.partition(" ")[2].partition(" ")[2])
        elif value == "/model live":
            self.models.show_models()
        elif value.startswith("/model live "):
            self.models.show_models(value.partition(" ")[2].partition(" ")[2])
        elif value.startswith("/model use "):
            self.models.select_model(value.partition(" ")[2].partition(" ")[2])
        elif value.startswith("/model "):
            model_value = value.partition(" ")[2]
            if model_value.isdigit():
                self.models.select_model(model_value)
            else:
                self.models.show_models(model_value)
        elif value == "/api-key set":
            self.actions.set_api_key()
        elif value == "/config show":
            self.actions.show_config()
        elif value == "/config reload":
            self.actions.reload_config()
        elif value == "/session":
            self.sessions.list_sessions()
        elif value.startswith("/session use "):
            self.sessions.switch_session(value.partition(" ")[2].partition(" ")[2])
        elif (value.startswith("/session ") and not value.startswith("/session rename ")
              and not value.startswith("/session export ") and not value.startswith("/session import ")):
            self.sessions.switch_session(value.partition(" ")[2].strip())
        elif value.startswith("/agent"):
            requested = value.partition(" ")[2].strip() or "supervisor"
            self.actions.set_agent(requested)
        elif value == "/cancel":
            self.actions.cancel_run()
        elif value.startswith("/session rename "):
            name = value.partition(" ")[2].partition(" ")[2].strip()
            self.sessions.rename_session(name)
        elif value.startswith("/session export "):
            destination = value.partition(" ")[2].partition(" ")[2].strip()
            self.sessions.export_session(destination)
        elif value.startswith("/session import "):
            source = value.partition(" ")[2].partition(" ")[2].strip()
            self.sessions.import_session(source)
        elif value.startswith("/remote connect ") or value.startswith("/remote use "):
            uri = value.partition(" ")[2].partition(" ")[2].strip()
            self.actions.connect_remote(uri)
        elif value in {"/remote disconnect", "/remote reset"}:
            self.actions.disconnect_remote()
        elif value == "/remote":
            self._write("Usage: /remote connect <ssh-uri> or /remote disconnect")
        elif value.startswith("/"):
            self._write(f"Unknown command: {value.split(maxsplit=1)[0]}. Type /help for commands.")
        else:
            if self._run_active:
                self._write("A run is already active. Use /cancel before starting another task.")
                return
            self._write(f"> {value}")
            self._run_active = True
            self._set_running(True)
            self.run_worker(self.runner.ask(value, plan_first=True), exclusive=False)


if App is not object:
    DevxTUI = DevTUI  # type: ignore[assignment]
else:
    class DevxTUI:  # pragma: no cover
        def __init__(self, session_id=None): self.session_id = session_id
        def run(self): raise RuntimeError("Install the TUI extra: pip install 'devx-coding-agent[tui]'")