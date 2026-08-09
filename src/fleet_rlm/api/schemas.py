"""HTTP schemas for the Fleet RLM public API."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from fleet_rlm.artifacts.models import ArtifactKind

SkillScope = Literal["system", "workspace"]
SkillTrust = Literal["system", "workspace", "untrusted"]


class SkillSelectionRequest(BaseModel):
    """One exact version-pinned Skill requested for the next Turn."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    expected_version: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$",
    )

    @model_validator(mode="after")
    def validate_expected_version(self) -> SkillSelectionRequest:
        if self.expected_version != self.expected_version.strip() or not self.expected_version.isprintable():
            raise ValueError("expected_version must be a printable non-whitespace value")
        return self


class CreateTurnRequest(BaseModel):
    """Canonical body for POST /api/sessions/{session_id}/turns."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=100_000)
    attachment_ids: list[UUID] = Field(default_factory=list)
    skill_selections: list[SkillSelectionRequest] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_canonical_input(self) -> CreateTurnRequest:
        if not self.text.strip():
            raise ValueError("text must contain a non-whitespace character")
        if len(self.attachment_ids) > 32:
            raise ValueError("at most 32 Attachments may be selected")
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError("attachment_ids must not contain duplicates")
        selection_ids = [selection.id for selection in self.skill_selections]
        if len(set(selection_ids)) != len(selection_ids):
            raise ValueError("skill_selections must not contain duplicate ids")
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


class SessionDetailResponse(SessionSummaryResponse):
    """Session detail — same public shape as the summary today."""


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


# ---------------------------------------------------------------------------
# Independent Workspace files (public files/ namespace only)
# ---------------------------------------------------------------------------


class WorkspaceFileEntryResponse(BaseModel):
    path: str
    kind: Literal["file", "directory"]
    byte_size: int | None = None
    modified_at: str | None = None
    checksum_sha256: str | None = None


class WorkspaceFileListResponse(BaseModel):
    entries: list[WorkspaceFileEntryResponse]
    truncated: bool = False
    next_cursor: str | None = None


class VolumeTreeResponse(BaseModel):
    """Logical files visible in the LocalScope Workspace Volume."""

    paths: list[str]
    directories: list[str] = Field(default_factory=list)
    truncated: bool = False


class WorkspaceFileReadResponse(BaseModel):
    path: str
    content: str
    next_cursor: str | None = None
    byte_size: int
    eof: bool


class WorkspaceFileWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1_024)
    content: str
    overwrite: bool
    expected_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class WorkspaceFileAppendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1_024)
    content: str
    expected_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class WorkspaceFileDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1_024)
    expected_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class WorkspaceFilePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1_024)
    old: str = Field(min_length=1)
    new: str
    expected_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class WorkspaceFileDeleteResponse(BaseModel):
    ok: bool = True
    path: str


# ---------------------------------------------------------------------------
# Local settings policy (config/fleet.toml; never .env or process secrets)
# ---------------------------------------------------------------------------


class SettingsFieldResponse(BaseModel):
    path: str
    group: str
    label: str
    value: JsonValue
    editor: Literal["text", "number", "boolean", "single_choice", "multi_choice"]
    choices: list[str] = Field(default_factory=list)
    environment_overridden: bool = False


class SettingsScopeResponse(BaseModel):
    name: str
    fields: list[SettingsFieldResponse]


class SettingsPolicyResponse(BaseModel):
    revision: str
    active_profile: str | None = None
    default_profile: str | None = None
    available_profiles: list[str] = Field(default_factory=list)
    restart_required: bool = True
    scopes: list[SettingsScopeResponse]


class SettingsPolicyPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    scope: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    path: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.]*$")
    value: JsonValue = None
    profile: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
