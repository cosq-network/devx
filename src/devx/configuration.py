from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {"adapter": "openai", "base_url": "https://api.openai.com/v1"},
    "openrouter": {"adapter": "openai", "base_url": "https://openrouter.ai/api/v1"},
    "anthropic": {"adapter": "anthropic", "base_url": "https://api.anthropic.com"},
    "google": {"adapter": "google", "base_url": ""},
    "azure": {"adapter": "azure", "base_url": ""},
    "ollama": {"adapter": "openai", "base_url": "http://localhost:11434/v1"},
}


class ConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelInfo:
    identifier: str
    context_length: int | None = None
    free: bool = False
    tool_calling: bool = False
    coding: bool = False

    @property
    def agent_ready(self) -> bool:
        """Whether the model is curated for coding and tool-enabled agent runs."""
        return self.tool_calling and self.coding


# Conservative, provider-specific defaults for the setup flow. These are
# intentionally curated for coding agents rather than being an exhaustive
# list of every model a provider exposes. Provider discovery remains available
# through the live ``/model`` command.
MODEL_CATALOG: dict[str, tuple[ModelInfo, ...]] = {
    "openai": (
        ModelInfo("gpt-4.1", tool_calling=True, coding=True),
        ModelInfo("gpt-4.1-mini", tool_calling=True, coding=True),
        ModelInfo("gpt-4o", tool_calling=True, coding=True),
        ModelInfo("gpt-4o-mini", tool_calling=True, coding=True),
        ModelInfo("o3-mini", tool_calling=True, coding=True),
    ),
    "openrouter": (
        ModelInfo("anthropic/claude-3.7-sonnet", tool_calling=True, coding=True),
        ModelInfo("deepseek/deepseek-chat", tool_calling=True, coding=True),
        ModelInfo("openai/gpt-4o", tool_calling=True, coding=True),
        ModelInfo("openrouter/free", context_length=200_000, free=True, tool_calling=True, coding=True),
        ModelInfo("poolside/laguna-s-2.1:free", free=True, tool_calling=True, coding=True),
        ModelInfo("qwen/qwen-2.5-coder-32b-instruct", tool_calling=True, coding=True),
    ),
    "anthropic": (
        ModelInfo("claude-3-5-sonnet-latest", tool_calling=True, coding=True),
        ModelInfo("claude-3-7-sonnet-latest", tool_calling=True, coding=True),
        ModelInfo("claude-sonnet-4-20250514", tool_calling=True, coding=True),
    ),
    "google": (
        ModelInfo("gemini-2.0-flash", tool_calling=True, coding=True),
        ModelInfo("gemini-2.5-flash", tool_calling=True, coding=True),
        ModelInfo("gemini-2.5-pro", tool_calling=True, coding=True),
    ),
    "azure": (
        ModelInfo("gpt-4o", tool_calling=True, coding=True),
        ModelInfo("gpt-4o-mini", tool_calling=True, coding=True),
        ModelInfo("gpt-4.1", tool_calling=True, coding=True),
    ),
    "ollama": (
        ModelInfo("qwen2.5-coder:7b", tool_calling=True, coding=True),
        ModelInfo("qwen2.5-coder:14b", tool_calling=True, coding=True),
        ModelInfo("llama3.1:8b", tool_calling=True, coding=True),
    ),
}


def catalog_models(provider: str) -> list[ModelInfo]:
    """Return the curated coding-agent models for a provider."""
    return list(MODEL_CATALOG.get(provider.lower().strip(), ()))


class UserConfig:
    """Manage user-wide settings without exposing secrets to session storage."""

    def __init__(self, path: Path | None = None):
        self.path = (path or Path.home() / ".devx" / "config.env").expanduser()

    def update(self, values: dict[str, str]) -> None:
        if any("\n" in value or "\r" in value for value in values.values()):
            raise ConfigurationError("Configuration values must not contain newlines")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
        remaining = dict(values)
        output: list[str] = []
        for line in existing:
            stripped = line.strip()
            key = stripped.split("=", 1)[0].removeprefix("export ").strip() if "=" in stripped else ""
            if key in remaining:
                output.append(f"{key}={remaining.pop(key)}")
            else:
                output.append(line)
        if output and output[-1].strip():
            output.append("")
        output.extend(f"{key}={value}" for key, value in remaining.items())
        self.path.write_text("\n".join(output) + "\n", encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def set_provider(self, provider: str) -> None:
        name = provider.lower().strip()
        if name not in PROVIDERS:
            raise ConfigurationError(f"Unknown provider: {provider}")
        details = PROVIDERS[name]
        values = {"DEVX_PROVIDER": name}
        if details["base_url"]:
            values["DEVX_BASE_URL"] = details["base_url"]
        self.update(values)

    def set_model(self, model: str) -> None:
        if not model.strip():
            raise ConfigurationError("Model identifier must not be empty")
        self.update({"DEVX_MODEL": model.strip()})

    def set_api_key(self, api_key: str) -> None:
        if not api_key.strip():
            raise ConfigurationError("API key must not be empty")
        self.update({"DEVX_API_KEY": api_key.strip()})

    def reset(
        self,
        keys: tuple[str, ...] = (
            "DEVX_PROVIDER",
            "DEV_PROVIDER",
            "DEVX_MODEL",
            "DEV_MODEL",
            "DEVX_BASE_URL",
            "DEV_BASE_URL",
            "DEVX_API_KEY",
            "DEV_API_KEY",
        ),
    ) -> None:
        """Remove provider, model, and credential configuration keys."""
        if not self.path.exists():
            return
        existing = self.path.read_text(encoding="utf-8").splitlines()
        output: list[str] = []
        for line in existing:
            stripped = line.strip()
            key = stripped.split("=", 1)[0].removeprefix("export ").strip() if "=" in stripped else ""
            if key not in keys:
                output.append(line)
        cleaned = "\n".join(output).strip()
        if cleaned:
            self.path.write_text(cleaned + "\n", encoding="utf-8")
        else:
            try:
                self.path.unlink()
            except OSError:
                self.path.write_text("", encoding="utf-8")


def list_models(base_url: str, api_key: str = "") -> list[ModelInfo]:
    """Discover models from an OpenAI-compatible ``/models`` endpoint."""
    endpoint = base_url.rstrip("/") + "/models"
    headers = {"Accept": "application/json", "User-Agent": "devx-coding-agent"}
    if api_key and api_key != "sk-placeholder":
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(endpoint, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=10) as response:  # nosec B310 - URL comes from configured provider.
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, ValueError) as exc:
        raise ConfigurationError(f"Unable to discover models from {endpoint}: {exc}") from exc
    models: list[ModelInfo] = []
    for item in payload.get("data", []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        identifier = str(item["id"])
        pricing = item.get("pricing", {}) or {}
        free = (
            identifier.endswith(":free")
            or identifier.endswith("/free")
            or identifier == "openrouter/free"
            or (pricing.get("prompt") == "0" and pricing.get("completion") == "0")
        )
        parameters = item.get("supported_parameters", []) or []
        tool_calling = "tools" in parameters or identifier == "openrouter/free"
        models.append(ModelInfo(identifier, item.get("context_length"), free, tool_calling))
    return sorted(models, key=lambda model: model.identifier.lower())


def mask_secret(value: str) -> str:
    if not value or value == "sk-placeholder":
        return "<not configured>"
    return value[:4] + "…" + value[-4:] if len(value) > 10 else "<configured>"
