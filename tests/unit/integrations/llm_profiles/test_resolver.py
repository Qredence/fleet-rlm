"""Unit tests for LLM profile resolver env mirroring."""

from __future__ import annotations

from uuid import uuid4

from fleet_rlm.integrations.config.env_file import (
    RUNTIME_SETTINGS_ALLOWLIST,
    normalize_updates,
)
from fleet_rlm.integrations.llm_profiles.resolver import (
    build_lm_kwargs_from_resolved,
    env_resolved_model_name,
    mirror_role_configs_to_env,
)
from fleet_rlm.integrations.llm_profiles.types import (
    WIRE_FORMAT_TO_MODEL_TYPE,
    ResolvedRoleLmConfig,
)


def test_mirror_role_configs_includes_delegate_api_base_in_allowlist() -> None:
    profile_id = uuid4()
    role_configs = {
        "planner": ResolvedRoleLmConfig(
            role="planner",
            profile_id=profile_id,
            profile_name="Planner Profile",
            model_id="gpt-4o",
            resolved_model_id="openai/gpt-4o",
            api_key="sk-planner",
            api_base="https://api.openai.com/v1",
        ),
        "delegate": ResolvedRoleLmConfig(
            role="delegate",
            profile_id=profile_id,
            profile_name="Delegate Profile",
            model_id="gpt-4o-mini",
            resolved_model_id="openai/gpt-4o-mini",
            api_key="sk-delegate",
            api_base="https://delegate.example.com/v1",
        ),
        "delegate_small": None,
    }

    env_updates = mirror_role_configs_to_env(role_configs)

    assert env_updates["DSPY_DELEGATE_LM_API_BASE"] == "https://delegate.example.com/v1"
    normalized = normalize_updates(env_updates, allowlist=RUNTIME_SETTINGS_ALLOWLIST)
    assert normalized["DSPY_DELEGATE_LM_API_BASE"] == "https://delegate.example.com/v1"


def test_env_resolved_model_name_passes_through_prefixed_ids() -> None:
    # Gemini is folded into openai_chat_completion; users supply openai/-prefixed
    # ids themselves. env_resolved_model_name is a pass-through now.
    config = ResolvedRoleLmConfig(
        role="planner",
        profile_id=uuid4(),
        profile_name="Gemini",
        model_id="gemini-3.5-flash",
        resolved_model_id="openai/gemini-3.5-flash",
        api_key="test-key",
        api_base="https://generativelanguage.googleapis.com/v1beta/openai/",
    )

    assert env_resolved_model_name(config) == "openai/gemini-3.5-flash"


def test_build_lm_kwargs_from_resolved_uses_env_resolved_model_name() -> None:
    config = ResolvedRoleLmConfig(
        role="planner",
        profile_id=uuid4(),
        profile_name="Gemini",
        model_id="gemini-3.1-pro-preview",
        resolved_model_id="openai/gemini-3.1-pro-preview",
        api_key="test-key",
        api_base="https://generativelanguage.googleapis.com/v1beta/openai/",
    )

    kwargs = build_lm_kwargs_from_resolved(config, max_tokens=32)

    assert kwargs["model"] == "openai/gemini-3.1-pro-preview"
    assert kwargs["api_key"] == "test-key"
    assert kwargs["max_tokens"] == 32


def test_build_lm_kwargs_from_resolved_forwards_timeout_and_temperature() -> None:
    """Planner guardrails (timeout/temperature) must reach dspy.LM kwargs.

    Regression guard for tr-52a8d5b5d13d43ac102f7aba2aca9f58, where the hosted
    BYOK path constructed the planner LM with no ``timeout``/``temperature``
    (and no ``max_tokens``), letting a single stalled glm-5.2 call run 156s.
    """
    config = ResolvedRoleLmConfig(
        role="planner",
        profile_id=uuid4(),
        profile_name="Planner",
        model_id="glm-5.2",
        resolved_model_id="openai/glm-5.2",
        api_key="test-key",
        api_base="https://api.example.com/v1",
    )

    kwargs = build_lm_kwargs_from_resolved(
        config,
        max_tokens=64000,
        timeout=60.0,
        temperature=0.7,
    )

    assert kwargs["max_tokens"] == 64000
    assert kwargs["timeout"] == 60.0
    assert kwargs["temperature"] == 0.7


def test_build_lm_kwargs_from_resolved_omits_unset_guardrails() -> None:
    """Unset guardrails must not pollute kwargs (dspy.LM uses its own defaults)."""
    config = ResolvedRoleLmConfig(
        role="planner",
        profile_id=uuid4(),
        profile_name="Planner",
        model_id="glm-5.2",
        resolved_model_id="openai/glm-5.2",
        api_key="test-key",
        api_base=None,
    )

    kwargs = build_lm_kwargs_from_resolved(config)

    assert "max_tokens" not in kwargs
    assert "timeout" not in kwargs
    assert "temperature" not in kwargs


def test_wire_format_to_model_type_literal_truth() -> None:
    """The three wire formats map to the two model_types DSPy supports."""
    assert WIRE_FORMAT_TO_MODEL_TYPE == {
        "openai_responses": "responses",
        "openai_chat_completion": "chat",
        "anthropic_messages": "chat",
    }


def test_build_lm_kwargs_from_resolved_openai_responses_uses_responses_path() -> None:
    """openai_responses routes through the Responses API and emits
    ``max_output_tokens`` (litellm silently drops ``max_tokens`` there)."""
    config = ResolvedRoleLmConfig(
        role="planner",
        profile_id=uuid4(),
        profile_name="OpenAI",
        model_id="gpt-4o",
        resolved_model_id="openai/gpt-4o",
        api_key="sk-openai",
        api_base="https://api.openai.com/v1",
        provider_type="openai_responses",
    )

    kwargs = build_lm_kwargs_from_resolved(config, max_tokens=512)

    assert kwargs["model_type"] == "responses"
    assert kwargs["max_output_tokens"] == 512
    assert "max_tokens" not in kwargs


def test_build_lm_kwargs_from_resolved_openai_chat_completion_uses_chat_path() -> None:
    """An OpenAI-compatible endpoint (e.g. Alibaba MaaS qwen) uses Chat
    Completions and the standard ``max_tokens`` kwarg."""
    config = ResolvedRoleLmConfig(
        role="planner",
        profile_id=uuid4(),
        profile_name="AliyunQwen",
        model_id="qwen3.7-plus-2026-05-26",
        resolved_model_id="qwen3.7-plus-2026-05-26",
        api_key="sk-qwen",
        api_base="https://ws-h5uq3u25sfeoxrke.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        provider_type="openai_chat_completion",
    )

    kwargs = build_lm_kwargs_from_resolved(config, max_tokens=8192)

    assert kwargs["model_type"] == "chat"
    assert kwargs["max_tokens"] == 8192
    assert "max_output_tokens" not in kwargs
    # Bare model + custom api_base → explicit provider hint for litellm routing.
    assert kwargs["custom_llm_provider"] == "openai"
