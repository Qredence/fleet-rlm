"""Resolve role bindings and profile credentials into runtime LM configuration."""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from .model_catalog import _litellm_model_id
from .store import LlmProfileStore, decrypt_profile_api_key
from .types import (
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
    litellm_model = _litellm_model_id(profile.provider_type, binding.model_id)
    return ResolvedRoleLmConfig(
        role=role,
        profile_id=profile.id,
        profile_name=profile.name,
        model_id=binding.model_id,
        litellm_model=litellm_model,
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


def env_litellm_model_name(config: ResolvedRoleLmConfig) -> str:
    """Map profile-native Google ids to LiteLLM-prefixed env identifiers."""
    model = config.litellm_model.strip()
    if model.startswith("gemini-") and "/" not in model:
        return f"openai/{model}"
    if model.startswith("gemini/gemini-"):
        return f"openai/{model.removeprefix('gemini/')}"
    return model


def mirror_role_configs_to_env(role_configs: dict[LlmRoleName, ResolvedRoleLmConfig | None]) -> dict[str, str]:
    updates: dict[str, str] = {}
    for role, env_keys in ROLE_ENV_KEYS.items():
        config = role_configs.get(role)
        if config is None:
            continue
        updates[env_keys["model"]] = env_litellm_model_name(config)
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
    kwargs: dict[str, Any] = {
        "model": config.litellm_model,
        "api_key": config.api_key,
    }
    if config.api_base:
        kwargs["api_base"] = config.api_base
    # OpenAI-/Anthropic-compatible endpoints that are not LiteLLM proxies use a raw
    # model id (no provider prefix); pass an explicit provider hint so litellm
    # routes the bare model name against the custom api_base. For Anthropic,
    # litellm then appends "/v1/messages" to the api_base and sends x-api-key +
    # anthropic-version. LiteLLM-proxy and real-OpenAI/Anthropic profiles keep
    # their prefixed model id and need no hint.
    if config.api_base and "/" not in config.litellm_model:
        if config.provider_type == "openai_compatible":
            kwargs["custom_llm_provider"] = "openai"
        elif config.provider_type == "anthropic_compatible":
            kwargs["custom_llm_provider"] = "anthropic"
    if max_tokens is not None:
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


def infer_provider_type_from_model(model_id: str, *, api_base: str | None = None) -> LlmProviderType:
    if model_id.startswith("anthropic/"):
        return "anthropic"
    if model_id.startswith("gemini/") or "gemini" in model_id:
        return "google"
    if model_id.startswith("openai/"):
        effective_api_base = api_base if api_base is not None else os.getenv("DSPY_LM_API_BASE", "").strip()
        return "openai_compatible" if effective_api_base else "openai"
    return "openai_compatible"
