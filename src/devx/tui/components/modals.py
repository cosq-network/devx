from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.screen import ModalScreen
    from textual.widgets import Button, Input, Label, Select, Static
else:
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal, Vertical
        from textual.screen import ModalScreen
        from textual.widgets import Button, Input, Label, Select, Static
    except ImportError:
        class App:
            pass
        
        class ComposeResult:
            pass
        
        class Horizontal:
            pass
        
        class Vertical:
            pass
        
        class ModalScreen:
            pass
        
        class Button:
            pass
        
        class Input:
            pass
        
        class Label:
            pass
        
        class Select:
            pass
        
        class Static:
            pass

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


class ApprovalModal(ModalScreen):
    def __init__(self, request: ApprovalRequest, done: Callable[[ApprovalDecision], None]):
        super().__init__()
        self.request, self.done = request, done

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-dialog"):
            yield Label(f"Approve {self.request.action}: {self.request.target}", id="approval-summary")
            yield Label("Reason", id="approval-reason-label")
            yield Static(self.request.reason, markup=False, id="approval-preview")
            yield Label(f"Action: {self.request.action} | Target: {self.request.target}", id="approval-details")
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
    def __init__(self, done: Callable[[str], None]):
        super().__init__()
        self.done = done

    def compose(self) -> ComposeResult:
        with Vertical(id="secret-dialog"):
            yield Label("Enter API key (input is masked; it will not be shown in the conversation)")
            yield Input(placeholder="API key", password=True, id="secret-input")
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
    def __init__(self, settings, done: Callable[[], None], *, first_time: bool = True):
        super().__init__()
        self.settings = settings
        self.done = done
        self.first_time = first_time

    def compose(self) -> ComposeResult:
        from devx.configuration import PROVIDERS, catalog_models

        options = [(name.title(), name) for name in sorted(PROVIDERS)]
        provider = self.settings.provider if self.settings.provider in PROVIDERS else "openrouter"
        models = catalog_models(provider)
        model_options = self._model_options(models)
        selected_model = self.settings.model if self.settings.model in {item.identifier for item in models} else (
            models[0].identifier if models else Select.BLANK
        )
        with Vertical(id="setup-dialog"):
            title = "First-time provider setup" if self.first_time else "Provider and model setup"
            help_text = (
                "Configure a provider before starting your first agent run."
                if self.first_time else "Choose a provider and coding model for future agent runs."
            )
            yield Label(title, id="setup-title")
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
        model_select.set_options(self._model_options(models))
        model_select.value = models[0].identifier if models else Select.BLANK

    @staticmethod
    def _model_options(models) -> list[tuple[str, str]]:
        return model_options(models)

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
        if provider == str(Select.BLANK):
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


class PlanPreviewModal(ModalScreen):
    """Modal to preview and confirm a generated plan before execution."""
    def __init__(self, plan: str, done: Callable[[bool], None]):
        super().__init__()
        self.plan = plan
        self.done = done

    def compose(self) -> ComposeResult:
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