"""Shared types for LLM provider profiles.

`LlmProviderType` names exactly three provider-neutral wire formats — the
choice a user makes at profile-creation time. The type name itself is the
contract: `openai_responses` maps to `dspy.LM(model_type="responses")`,
`openai_chat_completion` and `anthropic_messages` both map to
`model_type="chat"` (Anthropic Messages is reached via `model="anthropic/..."`
which LiteLLM routes to `POST /v1/messages`; there is no `model_type="messages"`
in DSPy — see https://dspy.ai/api/models/LM).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

LlmProviderType = Literal["openai_responses", "openai_chat_completion", "anthropic_messages"]
LlmRoleName = Literal["planner", "delegate", "delegate_small"]

# Per-wire-format default API base used when a profile does not specify one.
# Empty string means "user must supply api_base" (typical for BYOK custom
# gateways like Alibaba MaaS, OpenRouter, vLLM, Ollama, Anthropic-compatible proxies).
PROVIDER_DEFAULT_API_BASES: dict[LlmProviderType, str] = {
    "openai_responses": "https://api.openai.com/v1",
    "openai_chat_completion": "",
    "anthropic_messages": "https://api.anthropic.com",
}

# Map each wire-format type to DSPy's `model_type` kwarg (confirmed
# `Literal["chat", "text", "responses"]` in dspy 3.3.0b1). `text` is unused.
WIRE_FORMAT_TO_MODEL_TYPE: dict[LlmProviderType, Literal["chat", "responses"]] = {
    "openai_responses": "responses",
    "openai_chat_completion": "chat",
    "anthropic_messages": "chat",
}

# `custom_llm_provider` hint forwarded to LiteLLM when a bare model id is sent
# against a custom `api_base`. `None` means "let LiteLLM infer from the model
# string prefix" — only valid for `openai_responses` where the model carries an
# `openai/` prefix at the canonical api.openai.com endpoint.
WIRE_FORMAT_TO_LITELLM_PROVIDER: dict[LlmProviderType, str | None] = {
    "openai_responses": None,
    "openai_chat_completion": "openai",
    "anthropic_messages": "anthropic",
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
    resolved_model_id: str
    api_key: str
    api_base: str | None
    provider_type: LlmProviderType = "openai_chat_completion"
