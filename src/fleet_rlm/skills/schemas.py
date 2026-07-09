"""Pydantic schemas for skill loading and bundle metadata."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class LoadSkillInput(BaseModel):
    name: str = Field(..., description="Skill name (without .md extension)")


class SkillScope(str, Enum):
    SESSION = "session"
    USER = "user"
    PROJECT = "project"
    ORG = "org"
    SYSTEM = "system"
    SCAFFOLD = "scaffold"


class SkillTrustLevel(str, Enum):
    TRUSTED = "trusted"
    COMMUNITY = "community"


class SkillPermissionMode(str, Enum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class SkillResourceKind(str, Enum):
    REFERENCE = "reference"
    SCRIPT = "script"
    ASSET = "asset"
    TEMPLATE = "template"


class SkillResourceItem(BaseModel):
    kind: SkillResourceKind | str
    path: str
    description: str | None = None


class SkillCatalogItem(BaseModel):
    name: str
    description: str
    scope: SkillScope | str
    trust_level: SkillTrustLevel | str
    source: str
    resource_count: int = 0


class LoadSkillOutput(BaseModel):
    status: str
    name: str
    scope: str | None = None
    path: str | None = None
    source: str | None = None
    instructions: str = ""
    resources: list[SkillResourceItem] = Field(default_factory=list)
    error: str | None = None


class ListSkillsOutput(BaseModel):
    status: str
    skills: list[SkillCatalogItem] = Field(default_factory=list)
    error: str | None = None


class ReadSkillResourceOutput(BaseModel):
    status: str
    name: str = ""
    path: str = ""
    content: str = ""
    error: str | None = None
    code: str | None = None


class SkillResource(BaseModel):
    kind: SkillResourceKind
    path: str
    description: str | None = None


class SkillMetadata(BaseModel):
    name: str
    description: str
    scope: SkillScope
    trust_level: SkillTrustLevel
    permission_mode: SkillPermissionMode
    source: str
    directory_style: bool


class SkillBundle(BaseModel):
    metadata: SkillMetadata
    instructions: str
    resources: list[SkillResource] = Field(default_factory=list)


class SkillCatalogEntry(BaseModel):
    name: str
    description: str
    scope: SkillScope
    trust_level: SkillTrustLevel
    source: str


class SkillVisibilityPolicy(BaseModel):
    visible_scopes: list[SkillScope] = Field(default_factory=lambda: list(SkillScope))
    excluded_skill_ids: list[str] = Field(default_factory=list)
    included_skill_ids: list[str] | None = None


class SkillRuntimeContext(BaseModel):
    volume_mount_path: str | None = None
    visibility: SkillVisibilityPolicy = Field(default_factory=SkillVisibilityPolicy)
    selected_skill_ids: list[str] = Field(default_factory=list)
    max_active_skills: int | None = None


class SkillValidationIssue(BaseModel):
    severity: Literal["error", "warning"] = Field(description="Validation issue severity.")
    code: str = Field(description="Stable machine-readable validation issue code.")
    message: str = Field(description="Human-readable validation issue message.")
    path: str | None = Field(default=None, description="Optional resource path related to the issue.")


class SkillValidationResult(BaseModel):
    valid: bool
    issues: list[SkillValidationIssue] = Field(default_factory=list)


__all__ = [
    "ListSkillsOutput",
    "LoadSkillInput",
    "LoadSkillOutput",
    "ReadSkillResourceOutput",
    "SkillBundle",
    "SkillCatalogEntry",
    "SkillCatalogItem",
    "SkillMetadata",
    "SkillPermissionMode",
    "SkillResource",
    "SkillResourceItem",
    "SkillResourceKind",
    "SkillRuntimeContext",
    "SkillScope",
    "SkillTrustLevel",
    "SkillValidationIssue",
    "SkillValidationResult",
    "SkillVisibilityPolicy",
]
