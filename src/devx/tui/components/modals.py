"""
Modal screens for the Textual TUI.

Provides:
- ProviderSelectionModal: screen-center popup for provider and model selection
- ProviderSetupModal: first-time/reconfiguration provider setup
- ApprovalModal: approval confirmation dialog
- SecretInputModal: API key input modal
- PlanPreviewModal: plan preview before execution
"""

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING, Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

if TYPE_CHECKING:
    from devx.harness.approval import ApprovalDecision, ApprovalRequest


def copy_to_clipboard(text: str, *, app=None) -> str:
    """Copy *text* using the OS-default clipboard mechanism and return a label.

    Delegates to :func:`devx.util.clipboard.copy_to_clipboard`, which layers the
    platform-native utility (``clip`` / ``pbcopy`` / ``wl-copy`` / ``xclip`` /
    ``xsel``), then the Textual OSC 52 terminal clipboard when *app* is a live
    Textual app, then stdlib tkinter. Raises :class:`RuntimeError` for empty
    *text* or when no mechanism is reachable.
    """
    from devx.util.clipboard import copy_to_clipboard as _copy

    return _copy(text, app=app)


def model_options(models) -> list[tuple[str, str]]:
    """Build (label, value) ``Select`` options from curated coding models.

    Only agent-ready models (tool-calling + coding) are offered, matching the
    setup flows for agent runs.
    """
    return [
        (f"{item.identifier}{' [free]' if item.free else ''}", item.identifier)
        for item in models
        if item.agent_ready
    ]


def _model_options(models) -> list[tuple[str, str]]:
    """Build ``Select`` options from model objects, flagging free models.

    Only agent-ready models (tool calling + coding) are offered.
    """
    return [
        (f"{item.identifier}{' [free]' if item.free else ''}", item.identifier)
        for item in models
        if item.agent_ready
    ]


def _set_model_options(model_select: Select, models, preferred=None) -> None:
    """Replace a model ``Select``'s options and keep a *valid* selection.

    ``Select.set_options`` always resets the selection (to blank, or to the
    first option when blank is not allowed), so the previously chosen model has
    to be restored explicitly - and only when it is still offered. The previous
    value is read *before* the reset.
    """
    options = _model_options(models)
    available = [value for _, value in options]
    if preferred is None:
        current = model_select.value
        preferred = "" if current == Select.BLANK else str(current)
    model_select.set_options(options)
    if preferred in available:
        model_select.value = preferred
    elif available:
        model_select.value = available[0]
    else:
        model_select.value = Select.BLANK


class ApprovalModal(ModalScreen):
    """Modal for approving or denying a requested action."""

    def __init__(self, request: ApprovalRequest, done: Callable[[ApprovalDecision], None]):
        super().__init__()
        self.request, self.done = request, done

    def compose(self) -> ComposeResult:
        from textual.widgets import Button, Label, Static

        with Vertical(id="approval-dialog"):
            yield Label(f"Approve {self.request.action}: {self.request.target}", id="approval-summary")
            yield Label("Reason", id="approval-reason-label")
            yield Static(self.request.reason, markup=False, id="approval-preview")
            yield Label(f"Action: {self.request.action} | Target: {self.request.target}", id="approval-details")
            with Horizontal(classes="dialog-actions"):
                yield Button("Approve", id="approve")
                yield Button("Deny", id="deny")
                yield Button("Cancel run", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from devx.harness.approval import ApprovalDecision

        decision = {
            "approve": ApprovalDecision.APPROVE,
            "deny": ApprovalDecision.DENY,
            "cancel": ApprovalDecision.CANCEL,
        }[event.button.id]
        self.done(decision)
        self.dismiss(decision)


class SecretInputModal(ModalScreen):
    """Modal for entering an API key (input is masked)."""

    def __init__(self, done: Callable[[str], None]):
        super().__init__()
        self.done = done

    def compose(self) -> ComposeResult:
        from textual.widgets import Input

        with Vertical(id="secret-dialog"):
            yield Label("Enter API key (input is masked; it will not be shown in the conversation)")
            yield Input(placeholder="API key", password=True, id="secret-input")
            with Horizontal(classes="dialog-actions"):
                yield Button("Save", id="save")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            value = self.query_one("#secret-input", Input).value
            self.done(value)
        self.dismiss()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "secret-input":
            self.done(event.value)
            self.dismiss()


class ProviderSetupModal(ModalScreen):
    """Modal for first-time or reconfiguration of provider and model settings.

    Appears as a centered popup dialog with provider selection, model catalog,
    API key input, and base URL configuration. Shares the same write path as
    ``devx init`` so both produce identical ``.devx/config.json`` + ``.env`` files.
    """

    def __init__(self, settings, done: Callable[[], None], *, first_time: bool = True, title: str = ""):
        super().__init__()
        self.settings = settings
        self.done = done
        self.first_time = first_time
        self.title = title or ("First-time provider setup" if self.first_time else "Provider and model setup")

    def compose(self) -> ComposeResult:
        from devx.configuration import PROVIDERS, catalog_models
        from textual.widgets import Input, Select

        options = [(name.title(), name) for name in sorted(PROVIDERS)]
        provider = self.settings.provider if self.settings.provider in PROVIDERS else "openrouter"
        models = catalog_models(provider)
        model_options = self._model_options(models)
        selected_model = self.settings.model if self.settings.model in {item.identifier for item in models} else (
            models[0].identifier if models else Select.BLANK
        )
        with Vertical(id="setup-dialog"):
            yield Label(self.title, id="setup-title")
            help_text = (
                "Configure a provider before starting your first agent run."
                if self.first_time else "Choose a provider and coding model for future agent runs."
            )
            yield Label(help_text, id="setup-help")
            yield Select(options, value=provider, prompt="Provider", id="setup-provider")
            yield Input(
                value="" if self.settings.api_key == "sk-placeholder" else self.settings.api_key,
                placeholder="API key (not required for Ollama)",
                password=True,
                id="setup-api-key"
            )
            yield Input(value=self.settings.base_url, placeholder="Base URL", id="setup-base-url")
            yield Select(model_options, value=selected_model, prompt="Coding model", id="setup-model")
            yield Label("", id="setup-error")
            with Horizontal(id="setup-actions"):
                yield Button("Save and continue", id="setup-save")
                yield Button("Exit", id="setup-exit")

    def on_mount(self) -> None:
        from textual.widgets import Select
        self.query_one("#setup-provider", Select).focus()

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss()

    def on_select_changed(self, event: Select.Changed) -> None:
        from devx.configuration import PROVIDERS, catalog_models

        if event.select.id != "setup-provider":
            return
        provider = str(event.value)
        base_url = PROVIDERS.get(provider, {}).get("base_url", "")
        if base_url:
            self.query_one("#setup-base-url", Input).value = base_url
        models = catalog_models(provider)
        model_select = self.query_one("#setup-model", Select)
        _set_model_options(model_select, models)

    @staticmethod
    def _model_options(models) -> list[tuple[str, str]]:
        # Build options from curated model objects, showing [free] tag when applicable.
        return _model_options(models)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "setup-exit":
            self.app.action_quit()
            return
        if event.button.id != "setup-save":
            return
        from devx.configuration import ConfigurationError, PROVIDERS, apply_init

        provider = str(self.query_one("#setup-provider", Select).value)
        api_key = self.query_one("#setup-api-key", Input).value.strip()
        base_url = self.query_one("#setup-base-url", Input).value.strip()
        model_value = self.query_one("#setup-model", Select).value
        model = "" if model_value in {None, Select.BLANK} else str(model_value).strip()
        if provider == Select.BLANK:
            self.query_one("#setup-error", Label).update("Select a provider.")
            return
        if provider != "ollama" and not api_key:
            self.query_one("#setup-error", Label).update("Enter an API key, or choose Ollama for a local model.")
            return
        if not model:
            self.query_one("#setup-error", Label).update("Model is required.")
            return
        needs_base_url = bool(PROVIDERS.get(provider, {}).get("base_url")) or provider == "ollama"
        if needs_base_url and not base_url:
            self.query_one("#setup-error", Label).update("Base URL is required.")
            return
        try:
            # Share the exact write path used by ``devx init`` so both produce
            # identical ``.devx/config.json`` + ``.env`` files (provider base-URL
            # canonicalization, embeddings flag, and merge semantics included).
            apply_init(
                self.settings.workspace,
                provider=provider,
                model=model,
                api_key=api_key,
                base_url=base_url,
            )
        except (ConfigurationError, OSError) as exc:
            self.query_one("#setup-error", Label).update(str(exc))
            return
        self.done()
        self.dismiss()


class ProviderSelectionModal(ModalScreen):
    """A dedicated screen-center popup for provider and model selection.

    Features:
    - Centered modal dialog with focus trap
    - Provider dropdown limited to the supported providers
    - Live search/filter over the model catalogue
    - Dynamic model list based on the selected provider
    - Configuration preview (base URL, API key status)
    - Action buttons: Select, Cancel, Auto-detect
    - Escape to close, Tab navigation supported
    - Professional styling inspired by modern code editor UIs

    The ``Select`` action persists the choice through
    :func:`devx.configuration.apply_init`, the same write path used by
    ``devx init``, so the TUI and the CLI always produce identical files.
    """

    DEFAULT_CSS = """
    #provider-selector {
        width: 72;
        max-width: 90%;
        height: auto;
        padding: 2 3;
        background: #0d0d0d;
        border: round $primary;
        align: center middle;
        outline: solid #303030;
        color: #e0e0e0;
    }
    #provider-selector .dialog-title {
        color: #c0c0c0;
        text-style: bold;
        margin: 0 0 2 0;
        padding: 0 0 1 0;
        border-bottom: thick #303030;
    }
    #provider-selector .form-field {
        margin: 0 0 2 0;
    }
    #provider-selector .details-text {
        color: #666666;
        margin: 0 0 2 0;
    }
    #provider-selector .action-btn {
        margin: 0 0 0 1;
        width: auto;
    }
    #provider-selector .cancel-btn {
        margin: 0 0 0 1;
    }
    #provider-selector .action-btn:hover {
        background: #1a1a1a;
    }
    #provider-selector .cancel-btn:hover {
        background: #2a2a2a;
    }
    #provider-selector Select {
        margin: 0 0 2 0;
    }
    #provider-selector #provider-search {
        margin: 0 0 2 0;
    }
    #provider-selector #provider-actions {
        height: auto;
    }
    """

    BINDINGS = [
        Binding("escape", "close_modal", "Close"),
        Binding("ctrl+p", "close_modal", "Close", show=False),
    ]

    def __init__(self, settings, done: Callable[[str, str], None] | None = None):
        super().__init__()
        self.settings = settings
        self.done = done  # callback(provider, model) when selection confirmed
        self._search_query = ""
        self._refreshing = False

    # ── composition ──────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        from devx.configuration import PROVIDERS, catalog_models

        # Initial provider and model
        initial_provider = self.settings.provider if self.settings.provider in PROVIDERS else "openrouter"
        initial_models = catalog_models(initial_provider)
        initial_model = self.settings.model if self.settings.model in {m.identifier for m in initial_models} else (
            initial_models[0].identifier if initial_models else ""
        )

        with Vertical(id="provider-selector"):
            yield Label("Select Provider and Model", id="dialog-title", classes="dialog-title")
            yield Input(placeholder="Search models...", id="provider-search", classes="form-field")
            yield Select(
                options=[(name.title(), name) for name in sorted(PROVIDERS)],
                value=initial_provider,
                prompt="Provider",
                id="provider-select",
                classes="form-field",
            )
            yield Select(
                options=self._model_options(initial_models),
                value=initial_model if initial_model else Select.BLANK,
                prompt="Coding model",
                id="model-select",
                classes="form-field",
            )
            yield Static("", id="provider-details", classes="details-text")
            with Horizontal(id="provider-actions", classes="dialog-actions"):
                yield Button("Select", id="select-btn", classes="action-btn")
                yield Button("Auto-detect", id="auto-detect-btn", classes="action-btn")
                yield Button("Cancel", id="cancel-btn", classes="cancel-btn")

    def on_mount(self) -> None:
        """Set initial focus and refresh model list."""
        self._refresh_model_list("")
        self.query_one("#provider-search", Input).focus()

    # ── widget accessors ─────────────────────────────────────────────────────

    def _provider_select_widget(self) -> Select:
        return self.query_one("#provider-select", Select)

    def _model_select_widget(self) -> Select:
        return self.query_one("#model-select", Select)

    def _details_widget(self) -> Static:
        return self.query_one("#provider-details", Static)

    # ── model list helpers ───────────────────────────────────────────────────

    @staticmethod
    def _model_options(models) -> list[tuple[str, str]]:
        """Build ``Select`` options from model objects, flagging free models."""
        return _model_options(models)

    @staticmethod
    def _select_value(value) -> str:
        """Normalise a ``Select`` value into a stripped string ("" when blank)."""
        return "" if value == Select.BLANK else str(value).strip()

    def _refresh_model_list(self, query: str = "") -> None:
        """Refresh the model dropdown based on the current provider selection."""
        from devx.configuration import catalog_models

        provider = self._select_value(self._provider_select_widget().value)
        models = catalog_models(provider)
        if query:
            models = [m for m in models if query.lower() in m.identifier.lower()]
        _set_model_options(self._model_select_widget(), models)
        self._update_details(provider)

    def _update_details(self, provider: str) -> None:
        """Update the details panel with the provider configuration preview."""
        from devx.configuration import PROVIDERS, catalog_models

        base_url = PROVIDERS.get(provider, {}).get("base_url", "") or self.settings.base_url
        api_key = self.settings.api_key or ""
        if provider == "ollama":
            key_state = "not required (local model)"
        elif api_key and api_key != "sk-placeholder":
            key_state = "configured"
        else:
            key_state = "missing - set it with /provider setup"
        model_count = len(self._model_options(catalog_models(provider)))
        self._details_widget().update(
            f"Provider: {provider}\n"
            f"Base URL: {base_url}\n"
            f"API key: {key_state}\n"
            f"Coding models available: {model_count}"
        )

    # ── events ───────────────────────────────────────────────────────────────

    def action_close_modal(self) -> None:
        """Close the popup without changing the configuration."""
        self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.action_close_modal()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "provider-search":
            return
        self._search_query = event.value.strip()
        self._refresh_model_list(self._search_query)

    def on_select_changed(self, event: Select.Changed) -> None:
        """Handle provider or model selection changes."""
        if self._refreshing:
            return
        if event.select.id == "provider-select":
            self._refresh_model_list(self._search_query)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses in the modal."""
        button_id = event.button.id

        if button_id == "select-btn":
            self._apply_selection()
        elif button_id == "cancel-btn":
            self.action_close_modal()
        elif button_id == "auto-detect-btn":
            await self._auto_detect_models()

    # ── actions ──────────────────────────────────────────────────────────────

    def _apply_selection(self) -> None:
        """Persist the selected provider/model through the shared init path."""
        from devx.configuration import PROVIDERS, ConfigurationError, apply_init

        provider = self._select_value(self._provider_select_widget().value)
        model = self._select_value(self._model_select_widget().value)

        if not provider:
            self.app.notify("Select a provider first.", severity="warning")
            return
        if not model:
            self.app.notify("Select a model first.", severity="warning")
            return

        base_url = PROVIDERS.get(provider, {}).get("base_url", "") or self.settings.base_url
        api_key = self.settings.api_key or ""
        if api_key == "sk-placeholder":
            # Never persist the placeholder - keep the stored key untouched.
            api_key = ""
        try:
            # Share the exact write path used by ``devx init`` so the TUI and
            # the CLI always produce identical ``.devx/config.json`` + ``.env``.
            apply_init(
                self.settings.workspace,
                provider=provider,
                model=model,
                api_key=api_key,
                base_url=base_url,
                embeddings=bool(self.settings.embeddings_enabled),
            )
        except (ConfigurationError, OSError) as exc:
            self.app.notify(f"Could not save selection: {exc}", severity="error")
            return
        if self.done:
            self.done(provider, model)
        self.app.notify(f"Selected: {provider} / {model}", severity="success")
        self.dismiss((provider, model))

    async def _auto_detect_models(self) -> None:
        """Discover the live model list from the selected provider API."""
        from devx.configuration import ConfigurationError, PROVIDERS, list_models

        provider = self._select_value(self._provider_select_widget().value)
        if not provider:
            self.app.notify("Please select a provider first.", severity="warning")
            return

        base_url = PROVIDERS.get(provider, {}).get("base_url", "") or self.settings.base_url
        api_key = self.settings.api_key or ""
        if api_key == "sk-placeholder":
            api_key = ""
        model_select = self._model_select_widget()
        model_select.disabled = True
        self.app.notify(f"Auto-detecting models for {provider}...", severity="information")
        try:
            models = await asyncio.to_thread(list_models, base_url, api_key)
        except ConfigurationError as exc:
            model_select.disabled = False
            self.app.notify(f"Auto-detect failed: {str(exc)[:120]}", severity="warning")
            return
        except Exception as exc:  # pragma: no cover - defensive
            model_select.disabled = False
            self.app.notify(f"Auto-detect failed: {str(exc)[:120]}", severity="error")
            return

        _set_model_options(model_select, models)
        model_select.disabled = False
        self.app.notify(f"Found {len(models)} models for {provider}", severity="success")


class PlanPreviewModal(ModalScreen):
    """Modal to preview and confirm a generated plan before execution."""
    def __init__(self, plan: str, done: Callable[[bool], None]):
        super().__init__()
        self.plan = plan
        self.done = done

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        with Vertical(id="plan-preview-dialog"):
            yield Label("Generated Plan", id="plan-title")
            yield Static(self.plan, markup=False, id="plan-content")
            with Horizontal(id="plan-actions"):
                yield Button("Proceed", id="proceed", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "proceed":
            self.done(True)
        else:
            self.done(False)
        self.dismiss()