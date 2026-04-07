"""Typed DTO helpers for DB repository operations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .models_enums import (
    ArtifactKind,
    JobStatus,
    JobType,
    MembershipRole,
    MemoryKind,
    MemoryScope,
    MemorySource,
    RunStatus,
    RunStepType,
    SandboxProvider,
    TenantStatus,
)


@dataclass(frozen=True)
class IdentityUpsertResult:
    tenant_id: uuid.UUID
    user_id: uuid.UUID | None = None
    tenant_status: TenantStatus | None = None
    membership_role: MembershipRole | None = None


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
    uri: str | None = None
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
