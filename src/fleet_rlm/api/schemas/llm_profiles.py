"""Schemas for LLM provider profile APIs."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

LlmProviderTypeSchema = Literal["openai_responses", "openai_chat_completion", "anthropic_messages"]
LlmRoleNameSchema = Literal["planner", "delegate", "delegate_small"]


class LlmProviderProfileResponse(BaseModel):
    id: UUID = Field(description="Stable provider profile identifier.")
    name: str = Field(description="Human-readable profile label shown in Settings.")
    provider_type: LlmProviderTypeSchema = Field(description="Provider integration type.")
    api_base: str = Field(default="", description="Configured API base URL for the provider.")
    api_key_masked: str = Field(default="", description="Masked API key preview for display.")
    has_api_key: bool = Field(default=False, description="Whether a stored API key is configured.")
    metadata_json: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional provider-specific metadata.",
    )


class LlmProviderProfileCreateRequest(BaseModel):
    name: str = Field(description="Human-readable profile label.")
    provider_type: LlmProviderTypeSchema = Field(description="Provider integration type.")
    api_base: str | None = Field(default=None, description="Optional API base URL override.")
    api_key: str = Field(default="", description="Provider API key to encrypt and store.")
    metadata_json: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional provider-specific metadata.",
    )


class LlmProviderProfileUpdateRequest(BaseModel):
    name: str | None = Field(default=None, description="Updated profile label.")
    provider_type: LlmProviderTypeSchema | None = Field(default=None, description="Updated provider type.")
    api_base: str | None = Field(default=None, description="Updated API base URL.")
    api_key: str | None = Field(default=None, description="Replacement API key when rotating credentials.")
    clear_api_key: bool = Field(default=False, description="When true, remove the stored API key.")
    metadata_json: dict[str, Any] | None = Field(default=None, description="Replacement metadata payload.")


class LlmModelCatalogEntry(BaseModel):
    id: str = Field(description="Provider-native model identifier.")
    label: str = Field(description="Display label for model dropdowns.")
    resolved_model_id: str = Field(description="Normalized/prefixed model identifier used by the runtime.")


class LlmModelCatalogResponse(BaseModel):
    profile_id: UUID = Field(description="Provider profile that supplied the catalog.")
    models: list[LlmModelCatalogEntry] = Field(
        default_factory=list,
        description="Models available for assignment from this profile.",
    )
    cached: bool = Field(default=True, description="Whether the response came from the in-memory cache.")
    error: str | None = Field(
        default=None,
        description="Provider fetch error when the catalog is empty or fell back to static models.",
    )


class LlmRoleBindingResponse(BaseModel):
    role: LlmRoleNameSchema = Field(description="Runtime role receiving the model binding.")
    profile_id: UUID | None = Field(default=None, description="Assigned provider profile identifier.")
    profile_name: str | None = Field(default=None, description="Assigned provider profile label.")
    model_id: str = Field(default="", description="Provider-native model identifier for the role.")


class LlmRoleBindingsResponse(BaseModel):
    bindings: list[LlmRoleBindingResponse] = Field(
        default_factory=list,
        description="Current planner, delegate, and delegate_small bindings.",
    )


class LlmRoleBindingUpdate(BaseModel):
    profile_id: UUID | None = Field(default=None, description="Provider profile to bind to the role.")
    model_id: str | None = Field(default=None, description="Provider-native model identifier for the role.")


class LlmRoleBindingsUpdateRequest(BaseModel):
    planner: LlmRoleBindingUpdate | None = Field(default=None, description="Planner role binding patch.")
    delegate: LlmRoleBindingUpdate | None = Field(default=None, description="Delegate role binding patch.")
    delegate_small: LlmRoleBindingUpdate | None = Field(
        default=None,
        description="Small delegate role binding patch.",
    )


class LlmImportEnvResponse(BaseModel):
    profile: LlmProviderProfileResponse = Field(description="Profile created from current DSPY_* env values.")
    bindings: list[LlmRoleBindingResponse] = Field(
        default_factory=list,
        description="Role bindings created from the imported environment values.",
    )
