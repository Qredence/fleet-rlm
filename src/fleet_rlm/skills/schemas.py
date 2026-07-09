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


class RunSkillScriptOutput(BaseModel):
    success: bool = False
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    artifact_id: str | None = None
    log_path: str | None = None
    error: str | None = None


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


class SkillWriteAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    STAGE = "stage"
    APPROVE = "approve"
    REJECT = "reject"


class SkillInstallSource(str, Enum):
    URL_SINGLE = "url_single"
    MANIFEST = "manifest"
    GITHUB_REPO = "github_repo"
    TAP = "tap"


class SkillInstallAction(str, Enum):
    INSTALL = "install"
    UPDATE = "update"
    QUARANTINE = "quarantine"


class SkillSecuritySeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SkillInstallPolicy(BaseModel):
    url_install_enabled: bool = False
    bundle_install_enabled: bool = False
    allowed_hosts: list[str] = Field(default_factory=list)
    tap_url: str | None = None
    max_url_bytes: int = 512 * 1024
    max_bundle_bytes: int = 5 * 1024 * 1024


class SkillProvenanceRecord(BaseModel):
    skill_name: str
    scope: SkillScope
    source: SkillInstallSource
    source_url: str | None = None
    repo: str | None = None
    ref: str | None = None
    subpath: str | None = None
    manifest_url: str | None = None
    tap_name: str | None = None
    trust_level: SkillTrustLevel = SkillTrustLevel.COMMUNITY
    content_hash: str
    upstream_content_hash: str | None = None
    installed_at: str
    updated_at: str | None = None
    last_checked_at: str | None = None
    drift_detected: bool = False
    scan_id: str | None = None


class SkillSecurityFinding(BaseModel):
    severity: SkillSecuritySeverity
    code: str
    message: str
    path: str | None = None


class SkillSecurityScanResult(BaseModel):
    scan_id: str
    skill_name: str
    scope: SkillScope
    findings: list[SkillSecurityFinding] = Field(default_factory=list)
    blocked: bool = False
    force_allowed: bool = True
    scanned_at: str
    content_hash: str | None = None


class SkillUpdateStatus(BaseModel):
    skill_name: str
    scope: SkillScope
    installed: bool
    drift_detected: bool = False
    content_hash: str | None = None
    upstream_content_hash: str | None = None
    provenance: SkillProvenanceRecord | None = None


class SkillApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class SkillWritePolicy(BaseModel):
    user_writes_enabled: bool = True
    session_writes_enabled: bool = True
    require_staging: bool = False
    agent_writes_require_staging: bool = True


class SkillWriteContext(BaseModel):
    volume_mount_path: str
    session_id: str | None = None
    user_id: str | None = None
    workspace_id: str | None = None
    org_id: str | None = None
    policy: SkillWritePolicy = Field(default_factory=SkillWritePolicy)
    actor: Literal["user", "agent", "admin"] = "user"
    admin_writable_scopes: list[SkillScope] = Field(default_factory=list)


class StagedSkillChange(BaseModel):
    id: str
    skill_name: str
    scope: SkillScope
    action: SkillWriteAction
    status: SkillApprovalStatus
    created_at: str
    raw_markdown: str | None = None
    actor: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    workspace_id: str | None = None
    org_id: str | None = None
    old_content_hash: str | None = None
    new_content_hash: str | None = None
    source_label: str | None = None
    reason: str | None = None


class SkillAuditRecord(BaseModel):
    timestamp: str
    skill_name: str
    scope: SkillScope
    action: SkillWriteAction | SkillInstallAction
    actor: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    workspace_id: str | None = None
    org_id: str | None = None
    old_content_hash: str | None = None
    new_content_hash: str | None = None
    source_label: str
    approval_status: SkillApprovalStatus | None = None
    reason: str | None = None
    staged_change_id: str | None = None


__all__ = [
    "ListSkillsOutput",
    "LoadSkillInput",
    "LoadSkillOutput",
    "ReadSkillResourceOutput",
    "RunSkillScriptOutput",
    "SkillApprovalStatus",
    "SkillAuditRecord",
    "SkillBundle",
    "SkillCatalogEntry",
    "SkillCatalogItem",
    "SkillInstallAction",
    "SkillInstallPolicy",
    "SkillInstallSource",
    "SkillMetadata",
    "SkillPermissionMode",
    "SkillProvenanceRecord",
    "SkillResource",
    "SkillResourceItem",
    "SkillResourceKind",
    "SkillRuntimeContext",
    "SkillScope",
    "SkillSecurityFinding",
    "SkillSecurityScanResult",
    "SkillSecuritySeverity",
    "SkillTrustLevel",
    "SkillUpdateStatus",
    "SkillValidationIssue",
    "SkillValidationResult",
    "SkillVisibilityPolicy",
    "SkillWriteAction",
    "SkillWriteContext",
    "SkillWritePolicy",
    "StagedSkillChange",
]
