"""Shared types for LLM provider profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

LlmProviderType = Literal["openai", "anthropic", "google", "openai_compatible", "litellm_proxy", "anthropic_compatible"]
LlmRoleName = Literal["planner", "delegate", "delegate_small"]

PROVIDER_DEFAULT_API_BASES: dict[LlmProviderType, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "openai_compatible": "",
    "litellm_proxy": "",
    "anthropic_compatible": "",
}

LITELLM_PROVIDER_PREFIX: dict[LlmProviderType, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "openai",
    "openai_compatible": "openai",
    "litellm_proxy": "openai",
    "anthropic_compatible": "anthropic",
}


@dataclass(slots=True)
class LlmProviderProfileRecord:
    id: UUID
    name: str
    provider_type: LlmProviderType
    api_base: str
    api_key_ciphertext: str
    metadata_json: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class LlmRoleBindingRecord:
    role: LlmRoleName
    profile_id: UUID | None
    model_id: str


@dataclass(slots=True)
class LlmProfileBundle:
    profiles: list[LlmProviderProfileRecord]
    role_bindings: list[LlmRoleBindingRecord]


@dataclass(slots=True)
class ResolvedRoleLmConfig:
    role: LlmRoleName
    profile_id: UUID | None
    profile_name: str | None
    model_id: str
    litellm_model: str
    api_key: str
    api_base: str | None
    provider_type: LlmProviderType = "openai_compatible"
