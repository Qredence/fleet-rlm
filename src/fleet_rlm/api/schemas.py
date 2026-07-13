"""HTTP schemas for the Fleet RLM public API."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from fleet_rlm.artifacts.models import ArtifactKind
from fleet_rlm.skills.models import SkillScope, SkillTrust


class CreateTurnRequest(BaseModel):
    """Canonical body for POST /api/sessions/{session_id}/turns."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=100_000)
    attachment_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_canonical_input(self) -> CreateTurnRequest:
        if not self.text.strip():
            raise ValueError("text must contain a non-whitespace character")
        if len(self.attachment_ids) > 32:
            raise ValueError("at most 32 Attachments may be selected")
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError("attachment_ids must not contain duplicates")
        return self


class AttachmentResponse(BaseModel):
    """Public attachment metadata — no host or Volume paths."""

    id: UUID
    filename: str
    content_type: str | None = None
    byte_size: int
    checksum_sha256: str


class ArtifactResponse(BaseModel):
    """Public artifact metadata — no host or Volume paths."""

    id: UUID
    session_id: UUID
    run_id: UUID
    kind: ArtifactKind
    title: str | None = None
    media_type: str
    byte_size: int
    checksum_sha256: str


class SkillCardResponse(BaseModel):
    """Bounded Skill discovery metadata — no instructions body."""

    id: UUID
    name: str
    description: str
    scope: SkillScope
    version: str
    trust: SkillTrust
    affordances: list[str]
    resources_available: bool


# ---------------------------------------------------------------------------
# Sessions (durable conversation CRUD)
# ---------------------------------------------------------------------------


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=255)


class SessionPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=255)
    status: Literal["active", "archived"] | None = None


class SessionSummaryResponse(BaseModel):
    id: UUID
    title: str
    status: Literal["active", "archived"]
    checkpoint_version: int
    created_at: str | None = None
    updated_at: str | None = None


class SessionDetailResponse(BaseModel):
    id: UUID
    title: str
    status: Literal["active", "archived"]
    checkpoint_version: int
    created_at: str | None = None
    updated_at: str | None = None


class SessionListResponse(BaseModel):
    items: list[SessionSummaryResponse]
    total: int
    offset: int
    limit: int
    has_more: bool


class UIMessagePart(BaseModel):
    """Closed reload part vocabulary; variant-specific values remain bounded JSON."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal[
        "text",
        "reasoning",
        "dynamic-tool",
        "step-start",
        "data-status",
        "data-step",
        "data-rlm-code",
        "data-rlm-output",
        "data-skill",
        "data-attachment",
        "data-warning",
        "data-artifact",
        "data-usage",
        "data-structured-result",
    ]
    text: str | None = None
    state: str | None = None
    id: str | None = None
    tool_name: str | None = Field(default=None, alias="toolName")
    tool_call_id: str | None = Field(default=None, alias="toolCallId")
    input: JsonValue = None
    output: JsonValue = None
    error_text: str | None = Field(default=None, alias="errorText")
    provider_executed: bool | None = Field(default=None, alias="providerExecuted")
    data: JsonValue = None


class UIMessageResponse(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    parts: list[UIMessagePart]
    metadata: dict[str, JsonValue] | None = None


class SessionTurnPageResponse(BaseModel):
    items: list[UIMessageResponse]
    next_after_sequence: int | None = None
