"""HTTP schemas for the Fleet RLM public API."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_serializer, model_validator

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


class UIMessagePartModel(BaseModel):
    """Strict base for one reload UIMessage part variant.

    Top-level ``None`` fields are omitted on dump so HTTP reload JSON matches
    the lean producer dicts in ``ui_message.py`` (no variant-local null padding).
    Nested ``data`` payloads may still contain explicit nulls.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_serializer(mode="wrap")
    def _omit_top_level_none(self, handler):
        payload = handler(self)
        return {key: value for key, value in payload.items() if value is not None}


class TextContentUIMessagePart(UIMessagePartModel):
    text: str
    state: str | None = None


class TextUIMessagePart(TextContentUIMessagePart):
    type: Literal["text"] = "text"


class ReasoningUIMessagePart(TextContentUIMessagePart):
    type: Literal["reasoning"] = "reasoning"


class DynamicToolUIMessagePart(UIMessagePartModel):
    type: Literal["dynamic-tool"] = "dynamic-tool"
    tool_name: str = Field(alias="toolName")
    tool_call_id: str = Field(alias="toolCallId")
    state: str
    input: JsonValue
    output: JsonValue = None
    error_text: str | None = Field(default=None, alias="errorText")
    provider_executed: bool | None = Field(default=None, alias="providerExecuted")


class StepStartUIMessagePart(UIMessagePartModel):
    type: Literal["step-start"] = "step-start"


class DataUIMessagePart(UIMessagePartModel):
    data: JsonValue


class IdentifiedDataUIMessagePart(DataUIMessagePart):
    id: str | None = None


class DataStatusUIMessagePart(DataUIMessagePart):
    type: Literal["data-status"] = "data-status"


class DataStepUIMessagePart(DataUIMessagePart):
    type: Literal["data-step"] = "data-step"


class DataRLMCodeUIMessagePart(DataUIMessagePart):
    type: Literal["data-rlm-code"] = "data-rlm-code"


class DataRLMOutputUIMessagePart(DataUIMessagePart):
    type: Literal["data-rlm-output"] = "data-rlm-output"


class DataSkillUIMessagePart(IdentifiedDataUIMessagePart):
    type: Literal["data-skill"] = "data-skill"


class DataAttachmentUIMessagePart(IdentifiedDataUIMessagePart):
    type: Literal["data-attachment"] = "data-attachment"


class DataWarningUIMessagePart(DataUIMessagePart):
    type: Literal["data-warning"] = "data-warning"


class DataArtifactUIMessagePart(IdentifiedDataUIMessagePart):
    type: Literal["data-artifact"] = "data-artifact"


class DataUsageUIMessagePart(DataUIMessagePart):
    type: Literal["data-usage"] = "data-usage"


class DataStructuredResultUIMessagePart(DataUIMessagePart):
    type: Literal["data-structured-result"] = "data-structured-result"


UIMessagePart = Annotated[
    TextUIMessagePart
    | ReasoningUIMessagePart
    | DynamicToolUIMessagePart
    | StepStartUIMessagePart
    | DataStatusUIMessagePart
    | DataStepUIMessagePart
    | DataRLMCodeUIMessagePart
    | DataRLMOutputUIMessagePart
    | DataSkillUIMessagePart
    | DataAttachmentUIMessagePart
    | DataWarningUIMessagePart
    | DataArtifactUIMessagePart
    | DataUsageUIMessagePart
    | DataStructuredResultUIMessagePart,
    Field(discriminator="type"),
]


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
    editor: Literal["text", "number", "boolean", "single_choice", "multi_choice", "string_list"]
    choices: list[str] = Field(default_factory=list)
    environment_overridden: bool = False
    origin: Literal["default", "inherited", "override"] = "default"
    can_reset: bool = False


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


class SettingsPolicyUpdate(BaseModel):
    """One set or reset operation in an atomic settings-policy batch."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "oneOf": [
                {
                    "title": "SetSettingsPolicyValue",
                    "required": ["value"],
                    "properties": {
                        "value": {"not": {"type": "null"}},
                        "unset": {"const": False},
                    },
                },
                {
                    "title": "ResetSettingsPolicyValue",
                    "required": ["unset"],
                    "properties": {"unset": {"const": True}},
                    "not": {"required": ["value"]},
                },
            ]
        },
    )

    scope: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    path: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.]*$")
    value: JsonValue = None
    unset: bool = False

    @model_validator(mode="after")
    def validate_operation(self) -> SettingsPolicyUpdate:
        if self.unset and "value" in self.model_fields_set:
            raise ValueError("reset settings updates cannot include a value")
        if not self.unset and ("value" not in self.model_fields_set or self.value is None):
            raise ValueError("settings updates require a value")
        return self


class SettingsPolicyPatchRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "oneOf": [
                {
                    "title": "UpdateSettingsPolicyField",
                    "required": ["revision", "scope", "path", "value"],
                    "properties": {
                        "scope": {"not": {"type": "null"}},
                        "path": {"not": {"type": "null"}},
                        "value": {"not": {"type": "null"}},
                    },
                    "not": {
                        "anyOf": [
                            {"required": ["profile"]},
                            {"required": ["updates"]},
                            {"required": ["default_profile"]},
                        ]
                    },
                },
                {
                    "title": "SelectSettingsPolicyProfile",
                    "required": ["revision", "profile"],
                    "properties": {"profile": {"not": {"type": "null"}}},
                    "not": {
                        "anyOf": [
                            {"required": ["scope"]},
                            {"required": ["path"]},
                            {"required": ["value"]},
                            {"required": ["updates"]},
                            {"required": ["default_profile"]},
                        ]
                    },
                },
                {
                    "title": "BatchUpdateSettingsPolicy",
                    "required": ["revision"],
                    "properties": {"default_profile": {"not": {"type": "null"}}},
                    "anyOf": [
                        {
                            "required": ["updates"],
                            "properties": {"updates": {"minItems": 1}},
                        },
                        {"required": ["default_profile"]},
                    ],
                    "not": {
                        "anyOf": [
                            {"required": ["scope"]},
                            {"required": ["path"]},
                            {"required": ["value"]},
                            {"required": ["profile"]},
                        ]
                    },
                },
            ]
        },
    )

    revision: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    scope: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    path: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.]*$")
    value: JsonValue = None
    profile: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    updates: list[SettingsPolicyUpdate] = Field(default_factory=list, max_length=128)
    default_profile: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )

    @model_validator(mode="after")
    def validate_operation_shape(self) -> SettingsPolicyPatchRequest:
        supplied = self.model_fields_set
        legacy_fields = supplied.intersection({"scope", "path", "value"})
        batch_fields = supplied.intersection({"updates", "default_profile"})
        if "profile" in supplied and (legacy_fields or batch_fields):
            raise ValueError("legacy profile selection cannot be combined with settings updates")
        if batch_fields and legacy_fields:
            raise ValueError("batch settings updates cannot be combined with legacy settings fields")
        if legacy_fields and (
            legacy_fields != {"scope", "path", "value"} or self.scope is None or self.path is None or self.value is None
        ):
            raise ValueError("legacy settings updates require scope, path, and value")
        if "profile" in supplied and self.profile is None:
            raise ValueError("legacy profile selection requires a profile")
        if "default_profile" in supplied and self.default_profile is None:
            raise ValueError("default profile updates require a profile")
        if not legacy_fields and "profile" not in supplied and not self.updates and self.default_profile is None:
            raise ValueError("settings update is empty")
        return self


# ---------------------------------------------------------------------------
# Health probes (liveness / readiness)
# ---------------------------------------------------------------------------


class HealthLivenessResponse(BaseModel):
    """Liveness payload — the process is serving HTTP; no dependency checks."""

    status: Literal["ok"]
    app: str
    version: str


class HealthReadinessResponse(BaseModel):
    """Readiness payload — composition installed and the database answers."""

    status: Literal["ready"]
    database: Literal["ok", "not_configured"]
