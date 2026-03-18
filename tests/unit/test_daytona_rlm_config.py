from __future__ import annotations

import pytest

from fleet_rlm.infrastructure.providers.daytona.config import (
    DaytonaConfigError,
    resolve_daytona_config,
    resolve_daytona_lm_runtime_config,
)


def test_resolve_daytona_config_requires_native_api_url():
    with pytest.raises(DaytonaConfigError, match="DAYTONA_API_URL"):
        resolve_daytona_config(
            {
                "DAYTONA_API_KEY": "key",
                "DAYTONA_API_BASE_URL": "https://api.daytona.example",
            }
        )


def test_resolve_daytona_config_accepts_native_env_names():
    config = resolve_daytona_config(
        {
            "DAYTONA_API_KEY": "key",
            "DAYTONA_API_URL": "https://api.daytona.example",
        }
    )

    assert config.api_key == "key"
    assert config.api_url == "https://api.daytona.example"
    assert config.target is None


def test_resolve_daytona_config_passes_through_target():
    config = resolve_daytona_config(
        {
            "DAYTONA_API_KEY": "key",
            "DAYTONA_API_URL": "https://api.daytona.example",
            "DAYTONA_TARGET": "europe",
        }
    )

    assert config.target == "europe"


def test_resolve_daytona_lm_runtime_config_prefers_dspy_small_model_contract():
    config = resolve_daytona_lm_runtime_config(
        {
            "DSPY_LM_MODEL": "openai/gemini/gemini-3.1-pro-preview",
            "DSPY_LM_SMALL_MODEL": "openai/gemini-3-flash-preview",
            "DSPY_LLM_API_KEY": "planner-key",
            "DSPY_LM_API_BASE": "https://litellm.example",
            "DSPY_DELEGATE_LM_MODEL": "legacy-delegate-model",
            "DSPY_DELEGATE_LM_API_KEY": "legacy-delegate-key",
            "DSPY_DELEGATE_LM_API_BASE": "https://legacy-delegate.example",
            "OPENAI_API_KEY": "should-not-be-used",
        }
    )

    assert config.model == "openai/gemini/gemini-3.1-pro-preview"
    assert config.api_key == "planner-key"
    assert config.api_base == "https://litellm.example"
    assert config.delegate_model == "openai/gemini-3-flash-preview"
    assert config.delegate_api_key == "planner-key"
    assert config.delegate_api_base == "https://litellm.example"


def test_resolve_daytona_lm_runtime_config_ignores_legacy_delegate_contract():
    config = resolve_daytona_lm_runtime_config(
        {
            "DSPY_LM_MODEL": "openai/gpt-4.1",
            "DSPY_LLM_API_KEY": "planner-key",
            "DSPY_LM_API_BASE": "https://planner.example",
            "DSPY_DELEGATE_LM_SMALL_MODEL": "openai/gpt-4.1-nano",
            "DSPY_DELEGATE_LM_API_KEY": "delegate-key",
            "DSPY_DELEGATE_LM_API_BASE": "https://delegate.example",
        }
    )

    assert config.delegate_model is None
    assert config.delegate_api_key is None
    assert config.delegate_api_base is None


def test_resolve_daytona_lm_runtime_config_does_not_fallback_to_openai_key():
    with pytest.raises(DaytonaConfigError, match="DSPY_LM_MODEL"):
        resolve_daytona_lm_runtime_config(
            {
                "DSPY_LM_MODEL": "openai/gemini/gemini-3.1-pro-preview",
                "OPENAI_API_KEY": "should-not-be-used",
            }
        )
