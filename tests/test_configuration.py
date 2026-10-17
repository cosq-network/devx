import json
from pathlib import Path

import pytest

from devx import configuration
from devx.configuration import (
    ConfigurationError,
    ModelInfo,
    UserConfig,
    catalog_models,
    list_models,
    mask_secret,
)


def test_user_config_updates_values_without_duplicate_keys(tmp_path: Path):
    config = UserConfig(tmp_path / "config.env")
    config.set_provider("openrouter")
    config.set_api_key("secret")
    config.update({"DEVX_API_KEY": "new-secret"})

    text = config.path.read_text(encoding="utf-8")
    assert "DEVX_PROVIDER=openrouter" in text
    assert "DEVX_BASE_URL=https://openrouter.ai/api/v1" in text
    assert text.count("DEVX_API_KEY=") == 1
    assert "DEVX_API_KEY=new-secret" in text


def test_user_config_reset_removes_keys_and_preserves_other_variables(tmp_path: Path):
    config = UserConfig(tmp_path / "config.env")
    config.set_provider("openrouter")
    config.set_model("test-model")
    config.set_api_key("test-key")
    config.update({"CUSTOM_VAR": "keep-me"})

    # Reset provider keys
    config.reset()

    text = config.path.read_text(encoding="utf-8")
    assert "DEVX_PROVIDER" not in text
    assert "DEVX_MODEL" not in text
    assert "DEVX_BASE_URL" not in text
    assert "DEVX_API_KEY" not in text
    assert "CUSTOM_VAR=keep-me" in text

    # When all keys are reset, file should be unlinked
    config.reset(keys=("CUSTOM_VAR",))
    assert not config.path.exists()


def test_secret_masking_never_returns_short_or_placeholder_secret():
    assert mask_secret("sk-placeholder") == "<not configured>"
    assert mask_secret("short") == "<configured>"
    assert mask_secret("sk-or-v1-abcdefgh") == "sk-o…efgh"


def test_set_provider_clears_stale_base_url_for_custom_url_providers(tmp_path: Path):
    config = UserConfig(tmp_path / "config.env")
    config.set_provider("openrouter")  # has a canonical base URL
    config.set_provider("google")  # no canonical base URL
    text = config.path.read_text(encoding="utf-8")
    assert "DEVX_PROVIDER=google" in text
    assert "DEVX_BASE_URL" not in text


def test_set_provider_rewrites_stale_base_url_for_url_providers(tmp_path: Path):
    config = UserConfig(tmp_path / "config.env")
    config.update({"DEVX_BASE_URL": "http://stale.example.test"})
    config.set_provider("openai")
    text = config.path.read_text(encoding="utf-8")
    assert "DEVX_BASE_URL=https://api.openai.com/v1" in text


def test_model_catalog_contains_only_agent_ready_models_for_each_provider():
    for provider in configuration.PROVIDERS:
        models = catalog_models(provider)
        assert models
        assert all(model.agent_ready for model in models)
        assert len({model.identifier for model in models}) == len(models)


def test_openrouter_free_model_in_catalog():
    models = catalog_models("openrouter")
    openrouter_free = next((m for m in models if m.identifier == "openrouter/free"), None)
    assert openrouter_free is not None
    assert openrouter_free.free is True
    assert openrouter_free.tool_calling is True
    assert openrouter_free.coding is True
    assert openrouter_free.context_length == 200_000
    assert openrouter_free.agent_ready is True


def test_list_models_sorts_and_identifies_free_and_tool_models(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({
                "data": [
                    {"id": "paid-model", "pricing": {"prompt": "1", "completion": "1"}},
                    {"id": "free-model:free", "supported_parameters": ["tools"]},
                    {"id": "openrouter/free", "context_length": 200000},
                    {"id": "zero-priced", "pricing": {"prompt": "0", "completion": "0"}},
                    {"name": "invalid-without-id"},
                ]
            }).encode()

    monkeypatch.setattr(configuration, "urlopen", lambda request, timeout: Response())

    models = list_models("https://example.test/v1", "secret")

    assert [model.identifier for model in models] == ["free-model:free", "openrouter/free", "paid-model", "zero-priced"]
    assert models[0] == ModelInfo("free-model:free", free=True, tool_calling=True)
    assert models[1] == ModelInfo("openrouter/free", 200000, free=True, tool_calling=True)
    assert models[3].free is True


def test_list_models_converts_provider_errors_to_configuration_error(monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("offline")

    monkeypatch.setattr(configuration, "urlopen", fail)

    with pytest.raises(ConfigurationError, match="Unable to discover models"):
        list_models("https://example.test/v1")


def test_apply_init_resets_base_url_when_empty(tmp_path: Path):
    from devx.configuration import apply_init

    env_path = tmp_path / ".env"
    env_path.write_text("DEVX_BASE_URL=https://old.endpoint/v1\n", encoding="utf-8")

    result = apply_init(tmp_path, provider="google", model="gemini-2.0-flash", base_url="")
    assert result["base_url"] == ""

    saved = env_path.read_text(encoding="utf-8")
    assert "DEVX_PROVIDER=google" in saved
    assert "DEVX_MODEL=gemini-2.0-flash" in saved
    assert "DEVX_BASE_URL" not in saved

