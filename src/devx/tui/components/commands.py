from __future__ import annotations


from devx.config import Settings
from devx.configuration import PROVIDERS, ConfigurationError, catalog_models, list_models


HELP_TOPICS = {
    "setup": (
        "SETUP\n"
        "/setup or /configure  Open provider, API key, base URL, and coding-model setup.\n"
        "/api-key set           Change only the masked API key."
    ),
    "provider": (
        "PROVIDER\n"
        "/provider              List configured providers.\n"
        "/provider <number>     Select a listed provider.\n"
        "/provider use <name>   Select a provider by name.\n"
        "Use /setup when changing provider and model together."
    ),
    "model": (
        "MODEL\n"
        "/model                 Show curated coding models.\n"
        "/model <number>        Select a listed curated model.\n"
        "/model live             Discover models from the provider API.\n"
        "/model live tools      Show live tool-capable models.\n"
        "/model use <id>         Select a model by identifier."
    ),
    "session": (
        "SESSION\n"
        "/session                List saved sessions.\n"
        "/session use <id>       Switch and restore a saved session.\n"
        "/session rename <name>  Rename the current session.\n"
        "/session export <path>  Export the current session.\n"
        "/session import <path>  Import a session."
    ),
    "shortcuts": (
        "SHORTCUTS\n"
        "F1                     Show this help.\n"
        "Ctrl+L                 Clear the transcript.\n"
        "F2 / Ctrl+Insert       Copy selected or full transcript.\n"
        "Ctrl+C                 Copy a selection or cancel a run.\n"
        "Right arrow             Accept an autocomplete suggestion."
    ),
}


QUICK_START_HELP = (
    "QUICK START\n"
    "Describe a task in the prompt. Mention files with @path.\n"
    "First run: /setup    Change settings later: /setup\n"
    "\n"
    "COMMANDS\n"
    "/help [topic]         Show help or: setup, provider, model, session, shortcuts.\n"
    "/setup                 Configure provider and coding model.\n"
    "/provider              List or select a provider.\n"
    "/model                 Select a curated coding model.\n"
    "/session               List or switch saved sessions.\n"
    "/agent [name]          Choose supervisor, planner, coder, or tester.\n"
    "/config show           Show active settings with the API key masked.\n"
    "/copy /clear /diff      Copy, clear, or show the Git diff.\n"
    "/undo /plan <task>      Undo the last run or preview a plan.\n"
    "/cancel                 Cancel a running task.\n"
    "/exit                  Quit the TUI.\n"
    "\n"
    "Try /help model or /help shortcuts for details."
)


class HelpSystem:
    def __init__(self, app):
        self.app = app

    def show_help(self, topic: str = "") -> None:
        topic = topic.strip().lower()
        if topic in {"commands", "command", "all"}:
            topic = ""
        if topic:
            self.app._write(HELP_TOPICS.get(topic, f"No help topic: {topic}. Try /help."))
            return
        self.app._write(QUICK_START_HELP)


class ProviderManager:
    def __init__(self, app):
        self.app = app
        self._provider_choices: list[str] = []

    def show_providers(self) -> None:
        self._provider_choices = sorted(PROVIDERS)
        lines = ["Providers (use /provider <number> or /provider <name>):"]
        lines.extend(f"{index}. {name}" for index, name in enumerate(self._provider_choices, 1))
        self.app._write("\n".join(lines))

    def select_provider(self, value: str) -> None:
        value = value.strip()
        if value.isdigit():
            if not self._provider_choices:
                self._provider_choices = sorted(PROVIDERS)
            index = int(value)
            if not 0 < index <= len(self._provider_choices):
                self.app._write("Choose a provider number from the latest /provider list.")
                return
            value = self._provider_choices[index - 1]
        try:
            from devx.configuration import UserConfig
            UserConfig().set_provider(value)
            self.app._write(f"Provider saved: {value}. New runs will use it.")
        except ConfigurationError as exc:
            self.app._write(str(exc))


class ModelManager:
    def __init__(self, app):
        self.app = app
        self._model_choices: list[str] = []

    def show_catalog(self, filter_text: str = "") -> None:
        settings = Settings.load()
        query = filter_text.strip().lower()
        models = catalog_models(settings.provider)
        if query:
            models = [item for item in models if query in item.identifier.lower()]
        self._model_choices = [item.identifier for item in models]
        if not self._model_choices:
            self.app._write(f"No curated coding models found for provider: {settings.provider}.")
            return
        lines = [
            (
                f"Curated coding models for {settings.provider} "
                "(use /model <number> or /model use <id>):"
            )
        ]
        lines.extend(
            f"{index}. {item.identifier}"
            + (" [free]" if item.free else "")
            + " [tools, coding]"
            for index, item in enumerate(models, 1)
        )
        self.app._write("\n".join(lines))

    def show_models(self, filter_text: str = "") -> None:
        settings = Settings.load()
        try:
            models = list_models(settings.base_url, settings.api_key)
        except ConfigurationError as exc:
            self.app._write(str(exc))
            return
        query = filter_text.strip().lower()
        if query == "free":
            models = [item for item in models if item.free]
        elif query == "tools":
            models = [item for item in models if item.tool_calling]
        elif query:
            models = [item for item in models if query in item.identifier.lower()]
        self._model_choices = [item.identifier for item in models[:50]]
        if not self._model_choices:
            self.app._write("No matching models returned by the active provider.")
            return
        heading = "Models (use /model <number> or /model use <id>)"
        if query:
            heading += f" — filter: {filter_text}"
        lines = [heading]
        lines.extend(
            f"{index}. {item.identifier}"
            + (" [free]" if item.free else "")
            + (" [tools]" if item.tool_calling else "")
            for index, item in enumerate(models[:50], 1)
        )
        self.app._write("\n".join(lines))

    def select_model(self, value: str) -> None:
        value = value.strip()
        if value.isdigit():
            index = int(value)
            if not 0 < index <= len(self._model_choices):
                self.app._write("Choose a model number from the latest /model list.")
                return
            value = self._model_choices[index - 1]
        if not value:
            self.app._write("Enter a model name or run /model first.")
            return
        try:
            from devx.configuration import UserConfig
            UserConfig().set_model(value)
            self.app._write(f"Model saved: {value}. New runs will use it.")
        except ConfigurationError as exc:
            self.app._write(str(exc))