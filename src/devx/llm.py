from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from devx.config import Settings
from pydantic import SecretStr


class ModelConfigurationError(RuntimeError):
    pass


class ModelRateLimitError(RuntimeError):
    """Raised when the model provider returns a rate limit error."""
    pass


def _is_rate_limit_error(exc: Exception) -> bool:
    """Check if an exception is a rate limit error from any provider."""
    error_str = str(exc).lower()
    return any(keyword in error_str for keyword in [
        "rate limit", "rate_limit", "429", "too many requests",
        "quota exceeded", "throttl", "provider returned error"
    ])


def invoke_with_retry(model: Any, prompt: Any, *, attempts: int = 3, base_delay: float = 0.5):
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return model.invoke(prompt)
        except Exception as exc:
            last_error = exc
            # Retry on rate limits, timeouts, connection errors, and runtime errors
            if _is_rate_limit_error(exc) or isinstance(exc, (TimeoutError, ConnectionError, OSError, RuntimeError)):
                if attempt + 1 < attempts:
                    time.sleep(base_delay * (2 ** attempt))
                    continue
            # For non-retryable errors, raise immediately
            raise
    raise last_error or RuntimeError("Model invocation failed")


class ModelProvider(ABC):
    """Abstract base class for LLM model providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name (e.g., 'openai', 'anthropic')."""
        ...

    @property
    @abstractmethod
    def supports_azure(self) -> bool:
        """Whether this provider supports Azure deployment."""
        ...

    @abstractmethod
    def create_model(self, settings: Settings) -> Any:
        """Create a LangChain chat model from settings."""
        ...

    @abstractmethod
    def validate_settings(self, settings: Settings) -> None:
        """Validate that required settings are present for this provider."""
        ...


class OpenAIProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "openai"

    @property
    def supports_azure(self) -> bool:
        return True

    def validate_settings(self, settings: Settings) -> None:
        if not settings.api_key or settings.api_key == "sk-placeholder":
            raise ModelConfigurationError("OpenAI provider requires a valid API key")

    def create_model(self, settings: Settings) -> Any:
        from langchain_openai import ChatOpenAI
        kwargs = {
            "model": settings.model,
            "temperature": 0.1,
            "max_retries": 2,
        }
        if settings.api_key and settings.api_key != "sk-placeholder":
            kwargs["api_key"] = SecretStr(settings.api_key)
        if settings.provider == "azure":
            kwargs["azure_deployment"] = settings.model
            kwargs["api_version"] = "2024-02-15-preview"
            kwargs["azure_endpoint"] = settings.base_url
        else:
            kwargs["base_url"] = settings.base_url
        return ChatOpenAI(**kwargs)


class OpenRouterProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "openrouter"

    @property
    def supports_azure(self) -> bool:
        return False

    def validate_settings(self, settings: Settings) -> None:
        if not settings.api_key or settings.api_key == "sk-placeholder":
            raise ModelConfigurationError("OpenRouter provider requires a valid API key")
        if not settings.base_url:
            raise ModelConfigurationError("OpenRouter provider requires a base URL")

    def create_model(self, settings: Settings) -> Any:
        from langchain_openai import ChatOpenAI
        kwargs = {
            "model": settings.model,
            "base_url": settings.base_url,
            "temperature": 0.1,
            "max_retries": 2,
        }
        if settings.api_key and settings.api_key != "sk-placeholder":
            kwargs["api_key"] = SecretStr(settings.api_key)
        return ChatOpenAI(**kwargs)


class OllamaProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "ollama"

    @property
    def supports_azure(self) -> bool:
        return False

    def validate_settings(self, settings: Settings) -> None:
        if not settings.base_url:
            raise ModelConfigurationError("Ollama provider requires a base URL")

    def create_model(self, settings: Settings) -> Any:
        from langchain_openai import ChatOpenAI
        kwargs = {
            "model": settings.model,
            "base_url": settings.base_url,
            "temperature": 0.1,
            "max_retries": 2,
        }
        if settings.api_key and settings.api_key not in {"", "sk-placeholder"}:
            kwargs["api_key"] = SecretStr(settings.api_key)
        return ChatOpenAI(**kwargs)


class AnthropicProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def supports_azure(self) -> bool:
        return False

    def validate_settings(self, settings: Settings) -> None:
        if not settings.api_key or settings.api_key == "sk-placeholder":
            raise ModelConfigurationError("Anthropic provider requires a valid API key")

    def create_model(self, settings: Settings) -> Any:
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=settings.model,
            api_key=SecretStr(settings.api_key),
            temperature=0.1,
            max_retries=2,
        )


class GoogleProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "google"

    @property
    def supports_azure(self) -> bool:
        return False

    def validate_settings(self, settings: Settings) -> None:
        if not settings.api_key or settings.api_key == "sk-placeholder":
            raise ModelConfigurationError("Google provider requires a valid API key")

    def create_model(self, settings: Settings) -> Any:
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=settings.model,
            google_api_key=SecretStr(settings.api_key),
            temperature=0.1,
        )


class AzureProvider(ModelProvider):
    @property
    def name(self) -> str:
        return "azure"

    @property
    def supports_azure(self) -> bool:
        return True

    def validate_settings(self, settings: Settings) -> None:
        if not settings.api_key or settings.api_key == "sk-placeholder":
            raise ModelConfigurationError("Azure OpenAI provider requires a valid API key")
        if not settings.base_url:
            raise ModelConfigurationError("Azure OpenAI provider requires a base URL")

    def create_model(self, settings: Settings) -> Any:
        from langchain_openai import ChatOpenAI
        kwargs = {
            "model": settings.model,
            "temperature": 0.1,
            "max_retries": 2,
        }
        if settings.api_key and settings.api_key != "sk-placeholder":
            kwargs["api_key"] = SecretStr(settings.api_key)
        kwargs["azure_deployment"] = settings.model
        kwargs["api_version"] = "2024-02-15-preview"
        kwargs["azure_endpoint"] = settings.base_url
        return ChatOpenAI(**kwargs)


# Registry
_PROVIDERS: dict[str, ModelProvider] = {
    "openai": OpenAIProvider(),
    "openrouter": OpenRouterProvider(),
    "ollama": OllamaProvider(),
    "anthropic": AnthropicProvider(),
    "google": GoogleProvider(),
    "azure": AzureProvider(),
}


def register_provider(provider: ModelProvider) -> None:
    """Register a custom model provider."""
    _PROVIDERS[provider.name] = provider


def get_provider(name: str) -> ModelProvider:
    """Get a provider by name."""
    if name not in _PROVIDERS:
        available = ", ".join(sorted(_PROVIDERS.keys()))
        raise ModelConfigurationError(f"Unknown provider '{name}'. Available: {available}")
    return _PROVIDERS[name]


def create_llm(settings: Settings) -> Any:
    """Create a LangChain chat model from settings using the provider registry."""
    provider = get_provider(settings.provider)
    provider.validate_settings(settings)
    return provider.create_model(settings)


def list_providers() -> list[str]:
    """List all registered provider names."""
    return sorted(_PROVIDERS.keys())