"""Full-screen setup wizard for the interactive ``devx init`` path.

Plain ``devx init`` (no flags, interactive terminal) launches this wizard so
provider/model/API-key selection happens on one centered screen instead of a
sequence of line prompts. The wizard shares the write path with the CLI via
:func:`devx.configuration.apply_init`, so both produce identical
``.devx/config.json`` and ``.env`` files.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Button, Checkbox, Input, Label, Select
else:
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal, Vertical
        from textual.widgets import Button, Checkbox, Input, Label, Select
    except ImportError:  # pragma: no cover - textual is a core dependency
        class App:
            pass

        class ComposeResult:
            pass

        class Horizontal:
            pass

        class Vertical:
            pass

        class Button:
            pass

        class Checkbox:
            pass

        class Input:
            pass

        class Label:
            pass

        class Select:
            pass


def validate_init(provider: str, model: str, api_key: str, base_url: str = "") -> str | None:
    """Return an error message for the wizard fields, or ``None`` when valid."""
    from devx.configuration import PROVIDERS

    if not provider:
        return "Select a provider."
    if provider not in PROVIDERS:
        return f"{provider} is not a recognized provider."
    if provider != "ollama" and not api_key.strip():
        return "Enter an API key, or choose Ollama for a local model."
    if not base_url.strip() and not PROVIDERS[provider].get("base_url"):
        return f"{provider} requires a base URL."
    if not model.strip():
        return "Select a coding model."
    return None


def project_prefill(workspace: Path) -> dict[str, Any]:
    """Read existing project settings (``.devx/config.json``) for the wizard.

    Returns the raw project dict so it is preserved (merged) when the wizard
    saves; test/lint commands are no longer configured by ``devx init``.
    """
    path = workspace / ".devx" / "config.json"
    if not path.is_file():
        return {}
    try:
        import json

        project = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        project = {}
    return project if isinstance(project, dict) else {}


def _settings_prefill(settings: Any | None) -> dict[str, Any]:
    """Map an optional existing ``Settings`` instance onto wizard field defaults."""
    if settings is None:
        return {
            "provider": "ollama",
            "model": "",
            "api_key": "",
            "base_url": "",
            "embeddings": False,
        }
    from devx.configuration import PROVIDERS

    provider = getattr(settings, "provider", "") or ""
    if provider not in PROVIDERS:
        provider = "ollama"
    api_key = getattr(settings, "api_key", "") or ""
    if api_key == "sk-placeholder":
        api_key = ""
    return {
        "provider": provider,
        "model": getattr(settings, "model", "") or "",
        "api_key": api_key,
        "base_url": getattr(settings, "base_url", "") or "",
        "embeddings": bool(getattr(settings, "embeddings_enabled", False)),
    }


class InitWizard(App):
    """Centered, full-screen configuration wizard for ``devx init``."""

    TITLE = "devx • init"
    SUB_TITLE = "configure provider and model"
    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [("ctrl+c", "copy_or_quit", "Quit")]
    # Seconds between two Ctrl+C presses that quit the wizard.
    DOUBLE_CTRL_C_WINDOW: ClassVar[float] = 1.5
    CSS = """
    $primary: #303030;
    $accent: #bdbdbd;
    $surface: #111111;
    $panel: #1c1c1c;
    $text: #f2f2f2;
    $text-muted: #999999;
    $error: #ff6b6b;

    Screen {
        align: center middle;
        background: $surface;
    }
    #wizard-dialog {
        width: 78;
        height: auto;
        padding: 1 2;
        background: $panel;
        border: round $accent;
    }
    #wizard-title {
        text-style: bold;
        color: $text;
        padding-bottom: 1;
    }
    #wizard-help {
        color: $text-muted;
        padding-bottom: 1;
        text-wrap: wrap;
    }
    Label {
        color: $text-muted;
    }
    Select, Input {
        width: 100%;
        margin-bottom: 1;
    }
    #wizard-error {
        color: $error;
        height: 1;
    }
    #wizard-actions {
        width: 100%;
        height: 3;
        align: right middle;
        margin-top: 1;
    }
    Button {
        width: auto;
        margin-left: 1;
    }
    """

    def __init__(self, workspace: Path | None = None, settings: Any | None = None) -> None:
        super().__init__()
        self._workspace = Path(workspace or Path.cwd()).resolve()
        self._settings = settings
        self.cancelled = False
        self.result: dict[str, Any] | None = None
        # Double-press Ctrl+C-to-quit state (see DOUBLE_CTRL_C_WINDOW).
        self._ctrl_c_armed = False
        self._ctrl_c_at = 0.0

    def compose(self) -> ComposeResult:
        from devx.configuration import PROVIDERS, catalog_models
        from devx.tui.components.modals import model_options

        prefill = _settings_prefill(self._settings)

        models = catalog_models(prefill["provider"])
        selected_model = prefill["model"]
        if selected_model not in {item.identifier for item in models}:
            selected_model = models[0].identifier if models else Select.BLANK

        with Vertical(id="wizard-dialog"):
            yield Label("Configure devx for this project", id="wizard-title")
            yield Label(
                "Choose a provider and its coding model, and add an API key when "
                "needed. Changes are written to .env in the current directory.",
                id="wizard-help",
            )
            yield Select(
                [(name.title(), name) for name in sorted(PROVIDERS)],
                value=prefill["provider"],
                prompt="Provider",
                id="wizard-provider",
            )
            yield Select(
                model_options(models),
                value=selected_model,
                prompt="Coding model",
                id="wizard-model",
            )
            yield Input(
                value=prefill["api_key"],
                placeholder="API key (not required for Ollama)",
                password=True,
                id="wizard-api-key",
            )
            yield Input(
                value=prefill["base_url"],
                placeholder="Base URL",
                id="wizard-base-url",
            )
            yield Checkbox(
                "Enable local embeddings for semantic search",
                value=prefill["embeddings"],
                id="wizard-embeddings",
            )
            yield Label("", id="wizard-error")
            with Horizontal(id="wizard-actions"):
                yield Button("Save", id="wizard-save", variant="primary")
                yield Button("Cancel", id="wizard-cancel")

    def on_mount(self) -> None:
        self.query_one("#wizard-provider", Select).focus()

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self._cancel()

    def action_copy_or_quit(self) -> None:
        """Ctrl+C twice (within ``DOUBLE_CTRL_C_WINDOW``) quits the wizard."""
        now = time.monotonic()
        if self._ctrl_c_armed and (now - self._ctrl_c_at) <= self.DOUBLE_CTRL_C_WINDOW:
            self._ctrl_c_armed = False
            self.exit()
            return
        self._ctrl_c_at = now
        self._ctrl_c_armed = True
        try:
            self.query_one("#wizard-error", Label).update("Press Ctrl+C again to quit")
        except Exception:
            pass

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "wizard-provider":
            return
        self._refresh_provider_fields()

    def _refresh_provider_fields(self) -> None:
        from devx.configuration import PROVIDERS, catalog_models
        from devx.tui.components.modals import model_options

        provider = str(self.query_one("#wizard-provider", Select).value)
        self.query_one("#wizard-base-url", Input).value = PROVIDERS.get(provider, {}).get("base_url", "")
        models = catalog_models(provider)
        model_select = self.query_one("#wizard-model", Select)
        model_select.set_options(model_options(models))
        model_select.value = models[0].identifier if models else Select.BLANK

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "wizard-cancel":
            self._cancel()
            return
        if event.button.id != "wizard-save":
            return
        self._save()

    def _cancel(self) -> None:
        self.cancelled = True
        self.exit(0)

    def _save(self) -> None:
        from devx.configuration import apply_init

        provider = str(self.query_one("#wizard-provider", Select).value)
        if provider == "Select.BLANK":
            provider = ""
        model = str(self.query_one("#wizard-model", Select).value or "")
        if model == "Select.BLANK":
            model = ""
        api_key = self.query_one("#wizard-api-key", Input).value.strip()
        base_url = self.query_one("#wizard-base-url", Input).value.strip()

        error = validate_init(provider, model, api_key, base_url)
        if error:
            self.query_one("#wizard-error", Label).update(error)
            return

        embeddings = bool(self.query_one("#wizard-embeddings", Checkbox).value)
        effective_key = api_key or ("ollama" if provider == "ollama" else "")
        try:
            self.result = apply_init(
                self._workspace,
                provider=provider,
                model=model,
                api_key=effective_key,
                base_url=base_url,
                embeddings=embeddings,
                project=project_prefill(self._workspace),
            )
        except Exception as exc:  # ConfigurationError / OSError surfaced inline
            self.query_one("#wizard-error", Label).update(str(exc))
            return
        self.exit(0)