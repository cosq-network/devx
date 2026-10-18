from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from devx.config import Settings
from devx.llm import create_fallback_llm, invoke_with_retry


def test_create_fallback_llm_none_when_unconfigured(tmp_path: Path):
    s = Settings(workspace=tmp_path, fallback_model="")
    assert create_fallback_llm(s) is None


def test_create_fallback_llm_when_configured(tmp_path: Path):
    s = Settings(
        workspace=tmp_path,
        api_key="sk-test-key",
        provider="openai",
        model="gpt-4o",
        fallback_model="gpt-4o-mini",
    )
    fb = create_fallback_llm(s)
    assert fb is not None
    assert getattr(fb, "model_name", None) == "gpt-4o-mini"


def test_invoke_with_retry_falls_back_on_rate_limit():
    primary_model = MagicMock()
    primary_model.invoke.side_effect = RuntimeError("429 rate limit reached")

    fallback_model = MagicMock()
    fallback_model.invoke.return_value = SimpleNamespace(content="fallback plan succeeded")

    response = invoke_with_retry(
        primary_model,
        "test prompt",
        provider="openai",
        attempts=2,
        base_delay=0.01,
        fallback_model=fallback_model,
        fallback_provider="anthropic",
    )

    assert response.content == "fallback plan succeeded"
    assert primary_model.invoke.call_count == 2
    assert fallback_model.invoke.call_count == 1


def test_invoke_with_retry_raises_when_fallback_also_fails():
    primary_model = MagicMock()
    primary_model.invoke.side_effect = RuntimeError("429 rate limit")

    fallback_model = MagicMock()
    fallback_model.invoke.side_effect = RuntimeError("fallback also failed")

    with pytest.raises(RuntimeError, match="fallback also failed"):
        invoke_with_retry(
            primary_model,
            "test prompt",
            provider="openai",
            attempts=1,
            fallback_model=fallback_model,
        )
