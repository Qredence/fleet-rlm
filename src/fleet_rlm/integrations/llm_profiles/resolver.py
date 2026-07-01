"""Resolve role bindings and profile credentials into runtime LM configuration."""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from .model_catalog import _resolve_model_id
from .store import LlmProfileStore, decrypt_profile_api_key
from .types import (
    WIRE_FORMAT_TO_LITELLM_PROVIDER,
    WIRE_FORMAT_TO_MODEL_TYPE,
    LlmProfileBundle,
    LlmProviderProfileRecord,
    LlmProviderType,
    LlmRoleBindingRecord,
    LlmRoleName,
    ResolvedRoleLmConfig,
)

ROLE_ENV_KEYS: dict[LlmRoleName, dict[str, str]] = {
    "planner": {
        "model": "DSPY_LM_MODEL",
        "api_key": "DSPY_LLM_API_KEY",
        "api_base": "DSPY_LM_API_BASE",
    },
    "delegate": {
        "model": "DSPY_DELEGATE_LM_MODEL",
        "api_key": "DSPY_DELEGATE_LM_API_KEY",
        "api_base": "DSPY_DELEGATE_LM_API_BASE",
    },
    "delegate_small": {
        "model": "DSPY_DELEGATE_LM_SMALL_MODEL",
        "api_key": "DSPY_DELEGATE_LM_API_KEY",
        "api_base": "DSPY_DELEGATE_LM_API_BASE",
    },
}


def _profile_lookup(bundle: LlmProfileBundle) -> dict[UUID, LlmProviderProfileRecord]:
    return {profile.id: profile for profile in bundle.profiles}


def resolve_role_config(
    *,
    role: LlmRoleName,
    binding: LlmRoleBindingRecord,
    profile: LlmProviderProfileRecord | None,
) -> ResolvedRoleLmConfig | None:
    if profile is None or not binding.model_id.strip():
        return None
    api_key = decrypt_profile_api_key(profile)
    if not api_key:
        return None
    resolved_model_id = _resolve_model_id(profile.provider_type, binding.model_id)
    return ResolvedRoleLmConfig(
        role=role,
        profile_id=profile.id,
        profile_name=profile.name,
        model_id=binding.model_id,
        resolved_model_id=resolved_model_id,
        api_key=api_key,
        api_base=profile.api_base or None,
        provider_type=profile.provider_type,
    )


async def resolve_active_role_configs(store: LlmProfileStore) -> dict[LlmRoleName, ResolvedRoleLmConfig | None]:
    bundle = await store.load_bundle()
    profiles = _profile_lookup(bundle)
    resolved: dict[LlmRoleName, ResolvedRoleLmConfig | None] = {}
    for binding in bundle.role_bindings:
        profile = profiles.get(binding.profile_id) if binding.profile_id else None
        resolved[binding.role] = resolve_role_config(role=binding.role, binding=binding, profile=profile)
    return resolved


def env_resolved_model_name(config: ResolvedRoleLmConfig) -> str:
    """Return the resolved model id as it should be mirrored to env vars.

    All wire formats now use LiteLLM-recognized provider prefixes already
    (openai/ or anthropic/). Gemini users prefix with ``openai/`` themselves
    — Gemini is folded into ``openai_chat_completion``.
    """
    return config.resolved_model_id.strip()


def mirror_role_configs_to_env(role_configs: dict[LlmRoleName, ResolvedRoleLmConfig | None]) -> dict[str, str]:
    updates: dict[str, str] = {}
    for role, env_keys in ROLE_ENV_KEYS.items():
        config = role_configs.get(role)
        if config is None:
            continue
        updates[env_keys["model"]] = env_resolved_model_name(config)
        updates[env_keys["api_key"]] = config.api_key
        if config.api_base:
            updates[env_keys["api_base"]] = config.api_base
    return updates


def build_lm_kwargs_from_resolved(
    config: ResolvedRoleLmConfig,
    *,
    max_tokens: int | None = None,
    timeout: int | float | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"model": config.resolved_model_id, "api_key": config.api_key}
    if config.api_base:
        kwargs["api_base"] = config.api_base
        provider_hint = WIRE_FORMAT_TO_LITELLM_PROVIDER[config.provider_type]
        if provider_hint:
            # Bare model id on a custom api_base — tell LiteLLM which
            # transport to use so it doesn't crash with "LLM Provider NOT provided".
            kwargs["custom_llm_provider"] = provider_hint
    kwargs["model_type"] = WIRE_FORMAT_TO_MODEL_TYPE[config.provider_type]
    if max_tokens is not None:
        # The Responses endpoint drops `max_tokens` silently; it expects
        # `max_output_tokens`. DSPy 3.3.0b1 does not rename it for us.
        if config.provider_type == "openai_responses":
            kwargs["max_output_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
    if timeout is not None:
        kwargs["timeout"] = timeout
    if temperature is not None:
        kwargs["temperature"] = temperature
    return kwargs


def profile_labels_from_bundle(bundle: LlmProfileBundle) -> dict[LlmRoleName, tuple[str | None, str | None]]:
    """Return profile id and display name for each role binding."""
    profiles = _profile_lookup(bundle)
    labels: dict[LlmRoleName, tuple[str | None, str | None]] = {
        "planner": (None, None),
        "delegate": (None, None),
        "delegate_small": (None, None),
    }
    for binding in bundle.role_bindings:
        profile = profiles.get(binding.profile_id) if binding.profile_id else None
        labels[binding.role] = (
            str(binding.profile_id) if binding.profile_id else None,
            profile.name if profile else None,
        )
    return labels


def import_env_profile_payload() -> dict[str, str]:
    return {
        "planner_model": os.getenv("DSPY_LM_MODEL", "").strip(),
        "delegate_model": os.getenv("DSPY_DELEGATE_LM_MODEL", "").strip(),
        "delegate_small_model": os.getenv("DSPY_DELEGATE_LM_SMALL_MODEL", "").strip(),
        "api_base": os.getenv("DSPY_LM_API_BASE", "").strip(),
        "api_key": (os.getenv("DSPY_LLM_API_KEY", "").strip() or os.getenv("DSPY_LM_API_KEY", "").strip()),
    }


def infer_provider_type_from_model(
    model_id: str,
    *,
    api_base: str | None = None,
    api_base_env: str = "DSPY_LM_API_BASE",
) -> LlmProviderType:
    """Infer a wire-format type from a model id and optional api_base.

    Used by the env-var fallback path (no profile row). ``api_base`` (when
    provided) takes precedence; otherwise ``api_base_env`` is consulted.

    Mapping:
      - ``anthropic/<id>`` -> ``anthropic_messages``
      - ``openai/<id>`` without a custom api_base -> ``openai_responses``
        (canonical OpenAI endpoint, Responses API)
      - any other prefix or a bare id *with* a custom api_base ->
        ``openai_chat_completion``
      - bare id without api_base -> ``openai_chat_completion``
        (safe default; LiteLLM infers OpenAI for well-known ids)
    Gemini is folded into ``openai_chat_completion`` (it speaks OpenAI-compatible
    Chat Completions via its `/v1beta/openai/` endpoint — users prefix with ``openai/``).
    """
    resolved_api_base = api_base or os.getenv(api_base_env, "").strip()
    if model_id.startswith("anthropic/"):
        return "anthropic_messages"
    if model_id.startswith("openai/") and not resolved_api_base:
        return "openai_responses"
    return "openai_chat_completion"
