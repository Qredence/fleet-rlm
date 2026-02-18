"""Typed DTO helpers for DB repository operations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .models import (
    ArtifactKind,
    JobStatus,
    JobType,
    MemoryKind,
    MemoryScope,
    MemorySource,
    RunStatus,
    RunStepType,
    SandboxProvider,
    SkillLinkSource,
    SkillSource,
    SkillStatus,
    SkillUsageStatus,
)


@dataclass(frozen=True)
class IdentityUpsertResult:
    tenant_id: uuid.UUID
    user_id: uuid.UUID


@dataclass(frozen=True)
class SkillTaxonomyUpsertRequest:
    tenant_id: uuid.UUID
    key: str
    name: str
    created_by_user_id: uuid.UUID | None = None
    description: str | None = None


@dataclass(frozen=True)
class TaxonomyTermUpsertRequest:
    tenant_id: uuid.UUID
    taxonomy_id: uuid.UUID
    slug: str
    label: str
    parent_term_id: uuid.UUID | None = None
    description: str | None = None
    synonyms: list[str] = field(default_factory=list)
    sort_order: int = 0
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillUpsertRequest:
    tenant_id: uuid.UUID
    stable_key: str
    display_name: str
    created_by_user_id: uuid.UUID | None = None
    description: str | None = None
    source: SkillSource = SkillSource.SCAFFOLD
    status: SkillStatus = SkillStatus.ACTIVE
    latest_version: int | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillVersionCreateRequest:
    tenant_id: uuid.UUID
    skill_id: uuid.UUID
    version_num: int
    created_by_user_id: uuid.UUID | None = None
    semver: str | None = None
    manifest_json: dict[str, Any] = field(default_factory=dict)
    checksum: str | None = None
    source_uri: str | None = None
    is_current: bool = False


@dataclass(frozen=True)
class SkillTermLinkRequest:
    tenant_id: uuid.UUID
    skill_id: uuid.UUID
    term_id: uuid.UUID
    created_by_user_id: uuid.UUID | None = None
    confidence: float = 1.0
    is_primary: bool = False
    source: SkillLinkSource = SkillLinkSource.MANUAL


@dataclass(frozen=True)
class RunSkillUsageCreateRequest:
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    skill_id: uuid.UUID
    status: SkillUsageStatus = SkillUsageStatus.STARTED
    step_id: uuid.UUID | None = None
    skill_version_id: uuid.UUID | None = None
    invocation_name: str | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class RunCreateRequest:
    tenant_id: uuid.UUID
    created_by_user_id: uuid.UUID
    external_run_id: str
    status: RunStatus = RunStatus.RUNNING
    model_provider: str | None = None
    model_name: str | None = None
    sandbox_provider: SandboxProvider | None = None
    sandbox_session_id: uuid.UUID | None = None
    error_json: dict[str, Any] | None = None


@dataclass(frozen=True)
class RunStepCreateRequest:
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    step_index: int
    step_type: RunStepType
    input_json: dict[str, Any] | None = None
    output_json: dict[str, Any] | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None


@dataclass(frozen=True)
class ArtifactCreateRequest:
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    kind: ArtifactKind
    uri: str
    step_id: uuid.UUID | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryItemCreateRequest:
    tenant_id: uuid.UUID
    scope: MemoryScope
    scope_id: str
    kind: MemoryKind
    source: MemorySource
    content_text: str | None = None
    content_json: dict[str, Any] | None = None
    importance: int = 0
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class JobCreateRequest:
    tenant_id: uuid.UUID
    job_type: JobType
    idempotency_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: JobStatus = JobStatus.QUEUED
    max_attempts: int = 5
    available_at: datetime | None = None


@dataclass(frozen=True)
class JobLeaseRequest:
    tenant_id: uuid.UUID
    worker_id: str
    limit: int = 1
    available_before: datetime | None = None
    job_type: JobType | None = None
    lease_timeout_seconds: int = 300
