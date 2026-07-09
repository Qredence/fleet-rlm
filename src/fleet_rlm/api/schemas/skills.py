"""Pydantic schemas for Skills API routes (read-only and write)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fleet_rlm.skills.schemas import (
    SkillApprovalStatus,
    SkillResourceKind,
    SkillScope,
    SkillTrustLevel,
    SkillValidationIssue,
    SkillWriteAction,
)


class SkillVisibilityPolicyInput(BaseModel):
    """Optional visibility overrides for read-only skill inspection."""

    model_config = ConfigDict(extra="forbid")

    visible_scopes: list[SkillScope] | None = Field(
        default=None,
        description="Skill scopes visible to this request. Defaults to all scopes.",
    )
    excluded_skill_ids: list[str] = Field(default_factory=list, description="Skill ids to hide.")
    included_skill_ids: list[str] | None = Field(
        default=None,
        description="Optional allowlist of visible skill ids.",
    )


class SkillRuntimeContextInput(BaseModel):
    """Request body context fields supported by SkillRuntimeContext."""

    model_config = ConfigDict(extra="forbid")

    volume_mount_path: str | None = Field(default=None, description="Optional runtime volume mount path.")
    visibility: SkillVisibilityPolicyInput | None = Field(default=None, description="Optional visibility policy.")
    selected_skill_ids: list[str] = Field(default_factory=list, description="Explicit skill ids to prioritize.")
    max_active_skills: int | None = Field(default=None, ge=1, le=20, description="Maximum active skills to select.")


class SkillResourceItem(BaseModel):
    """Safe resource inventory entry."""

    model_config = ConfigDict(extra="forbid")

    kind: SkillResourceKind = Field(description="Resource kind.")
    path: str = Field(description="Skill-relative resource path.")
    description: str | None = Field(default=None, description="Optional resource description.")


class SkillCatalogItem(BaseModel):
    """Safe skill metadata exposed through the API."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Skill id.")
    description: str = Field(description="Skill description.")
    scope: SkillScope = Field(description="Skill scope.")
    trust_level: SkillTrustLevel = Field(description="Skill trust level.")
    source: str = Field(description="Safe source label without filesystem paths.")
    resource_count: int = Field(default=0, description="Number of inventoried resources.")


class SkillListResponse(BaseModel):
    """Response for visible skill catalog listing."""

    model_config = ConfigDict(extra="forbid")

    skills: list[SkillCatalogItem] = Field(default_factory=list, description="Visible skill metadata entries.")


class SkillDetailResponse(BaseModel):
    """Response for one visible skill."""

    model_config = ConfigDict(extra="forbid")

    skill: SkillCatalogItem = Field(description="Safe skill metadata.")
    resources: list[SkillResourceItem] = Field(default_factory=list, description="Safe resource inventory.")


class SkillSelectRequest(SkillRuntimeContextInput):
    """Request body for read-only skill selection."""

    model_config = ConfigDict(extra="forbid")

    user_request: str = Field(description="User request to route against visible skills.")
    core_memory: str = Field(default="", description="Optional recent context for bounded selector disambiguation.")
    execution_mode: str = Field(default="auto", description="Runtime execution mode hint.")
    routing_decision: str | None = Field(default=None, description="Optional routing decision hint.")
    is_first_turn: bool = Field(default=False, description="Whether the request is for the first turn.")


class SkillSelectionResponse(BaseModel):
    """Read-only skill selection result."""

    model_config = ConfigDict(extra="forbid")

    selected_skills: list[str] = Field(default_factory=list, description="Selected visible skill ids.")
    skill_context: str = Field(default="", description="Short active-skill summary.")
    catalog: dict[str, str] = Field(default_factory=dict, description="Selected skill descriptions by id.")
    sources: dict[str, str] = Field(default_factory=dict, description="Selected skill source labels by id.")
    warnings: list[str] = Field(default_factory=list, description="Safe selection warnings.")


class SkillLoadRequest(SkillRuntimeContextInput):
    """Request body for loading visible skill bundles."""

    model_config = ConfigDict(extra="forbid")

    names: list[str] = Field(description="Skill ids to load.")


class SkillBundleResponse(BaseModel):
    """Loaded visible skill bundle."""

    model_config = ConfigDict(extra="forbid")

    skill: SkillCatalogItem = Field(description="Safe skill metadata.")
    instructions: str = Field(description="SKILL.md markdown instructions.")
    resources: list[SkillResourceItem] = Field(default_factory=list, description="Safe resource inventory.")


class SkillLoadResponse(BaseModel):
    """Response for loading one or more visible skill bundles."""

    model_config = ConfigDict(extra="forbid")

    bundles: list[SkillBundleResponse] = Field(default_factory=list, description="Loaded visible skill bundles.")


class SkillValidateRequest(SkillRuntimeContextInput):
    """Request body for read-only skill validation."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, description="Known skill id or metadata name to validate.")
    description: str | None = Field(default=None, description="Metadata description to validate.")
    directory_name: str | None = Field(default=None, description="Optional directory name to compare with name.")
    resource_paths: list[str] = Field(default_factory=list, description="Skill-relative resource paths to validate.")
    raw_markdown: str = Field(default="", description="Optional SKILL.md markdown used for bundle validation.")


class SkillValidateResponse(BaseModel):
    """Validation result for skill metadata, bundles, or resource paths."""

    model_config = ConfigDict(extra="forbid")

    valid: bool = Field(description="Whether validation found no error-severity issues.")
    issues: list[SkillValidationIssue] = Field(default_factory=list, description="Validation issues.")


class SkillResourceContentResponse(BaseModel):
    """Response body for one safe skill resource."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Skill id.")
    path: str = Field(description="Skill-relative resource path.")
    content: str = Field(description="UTF-8 resource content.")


class SkillErrorDetail(BaseModel):
    """Structured safe error detail for Skills API failures."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Safe human-readable message.")
    detail: Any | None = Field(default=None, description="Optional structured safe detail.")


class SkillWriteCreateRequest(BaseModel):
    """Request body for creating a user- or session-scoped skill."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Skill id to create.")
    raw_markdown: str = Field(description="SKILL.md markdown content, including frontmatter.")
    session_id: str | None = Field(default=None, description="Optional session id recorded in audit metadata.")
    reason: str | None = Field(default=None, description="Optional reason recorded in audit metadata.")


class SkillWriteUpdateRequest(BaseModel):
    """Request body for updating a user- or session-scoped skill."""

    model_config = ConfigDict(extra="forbid")

    raw_markdown: str = Field(description="SKILL.md markdown content, including frontmatter.")
    session_id: str | None = Field(default=None, description="Optional session id recorded in audit metadata.")
    reason: str | None = Field(default=None, description="Optional reason recorded in audit metadata.")


class SkillWriteResponse(BaseModel):
    """Safe response for a direct or staged skill write."""

    model_config = ConfigDict(extra="forbid")

    skill_name: str = Field(description="Skill id.")
    scope: SkillScope = Field(description="Skill scope.")
    action: SkillWriteAction = Field(description="Write action performed.")
    status: Literal["committed", "staged"] = Field(
        description="Whether the write committed directly or was staged for approval."
    )
    staged_change_id: str | None = Field(default=None, description="Staged change id when the write was staged.")
    approval_status: SkillApprovalStatus | None = Field(
        default=None, description="Staged change approval status when applicable."
    )
    source: str | None = Field(default=None, description="Safe source label without filesystem paths.")


class SkillStagedApproveRequest(BaseModel):
    """Request body for approving a staged skill change."""

    model_config = ConfigDict(extra="forbid")


class SkillStagedRejectRequest(BaseModel):
    """Request body for rejecting a staged skill change."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, description="Optional rejection reason recorded in audit metadata.")


class SkillStagedActionResponse(BaseModel):
    """Safe response for a staged-change approval or rejection action."""

    model_config = ConfigDict(extra="forbid")

    staged_change_id: str = Field(description="Staged change id.")
    skill_name: str = Field(description="Skill id.")
    scope: SkillScope = Field(description="Skill scope.")
    action: SkillWriteAction = Field(description="Original staged write action.")
    status: Literal["approved", "rejected"] = Field(description="Approval action outcome.")
    approval_status: SkillApprovalStatus = Field(description="Final staged change approval status.")


__all__ = [
    "SkillBundleResponse",
    "SkillCatalogItem",
    "SkillDetailResponse",
    "SkillErrorDetail",
    "SkillListResponse",
    "SkillLoadRequest",
    "SkillLoadResponse",
    "SkillResourceContentResponse",
    "SkillResourceItem",
    "SkillRuntimeContextInput",
    "SkillSelectRequest",
    "SkillSelectionResponse",
    "SkillStagedActionResponse",
    "SkillStagedApproveRequest",
    "SkillStagedRejectRequest",
    "SkillValidateRequest",
    "SkillValidateResponse",
    "SkillVisibilityPolicyInput",
    "SkillWriteCreateRequest",
    "SkillWriteResponse",
    "SkillWriteUpdateRequest",
]
