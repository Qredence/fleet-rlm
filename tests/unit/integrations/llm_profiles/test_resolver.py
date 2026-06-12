"""Unit tests for LLM profile resolver env mirroring."""

from __future__ import annotations

from uuid import uuid4

from fleet_rlm.integrations.config.runtime_settings import (
    RUNTIME_SETTINGS_ALLOWLIST,
    normalize_updates,
)
from fleet_rlm.integrations.llm_profiles.resolver import (
    build_lm_kwargs_from_resolved,
    env_litellm_model_name,
    mirror_role_configs_to_env,
)
from fleet_rlm.integrations.llm_profiles.types import ResolvedRoleLmConfig


def test_mirror_role_configs_includes_delegate_api_base_in_allowlist() -> None:
    profile_id = uuid4()
    role_configs = {
        "planner": ResolvedRoleLmConfig(
            role="planner",
            profile_id=profile_id,
            profile_name="Planner Profile",
            model_id="gpt-4o",
            litellm_model="openai/gpt-4o",
            api_key="sk-planner",
            api_base="https://api.openai.com/v1",
        ),
        "delegate": ResolvedRoleLmConfig(
            role="delegate",
            profile_id=profile_id,
            profile_name="Delegate Profile",
            model_id="gpt-4o-mini",
            litellm_model="openai/gpt-4o-mini",
            api_key="sk-delegate",
            api_base="https://delegate.example.com/v1",
        ),
        "delegate_small": None,
    }

    env_updates = mirror_role_configs_to_env(role_configs)

    assert env_updates["DSPY_DELEGATE_LM_API_BASE"] == "https://delegate.example.com/v1"
    normalized = normalize_updates(env_updates, allowlist=RUNTIME_SETTINGS_ALLOWLIST)
    assert normalized["DSPY_DELEGATE_LM_API_BASE"] == "https://delegate.example.com/v1"


def test_env_litellm_model_name_prefixes_bare_google_ids() -> None:
    config = ResolvedRoleLmConfig(
        role="planner",
        profile_id=uuid4(),
        profile_name="Gemini",
        model_id="gemini-3.5-flash",
        litellm_model="openai/gemini-3.5-flash",
        api_key="test-key",
        api_base="https://generativelanguage.googleapis.com/v1beta/openai/",
    )

    assert env_litellm_model_name(config) == "openai/gemini-3.5-flash"


def test_build_lm_kwargs_from_resolved_uses_env_litellm_model_name() -> None:
    config = ResolvedRoleLmConfig(
        role="planner",
        profile_id=uuid4(),
        profile_name="Gemini",
        model_id="gemini-3.1-pro-preview",
        litellm_model="openai/gemini-3.1-pro-preview",
        api_key="test-key",
        api_base="https://generativelanguage.googleapis.com/v1beta/openai/",
    )

    kwargs = build_lm_kwargs_from_resolved(config, max_tokens=32)

    assert kwargs["model"] == "openai/gemini-3.1-pro-preview"
    assert kwargs["api_key"] == "test-key"
    assert kwargs["max_tokens"] == 32
