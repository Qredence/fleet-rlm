"""Typed DTO helpers for DB repository operations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .models_enums import (
    ArtifactKind,
    ArtifactProvider,
    ChatSessionStatus,
    ChatTurnStatus,
    DatasetFormat,
    DatasetSource,
    JobStatus,
    JobType,
    MembershipRole,
    MemoryKind,
    MemoryScope,
    MemorySource,
    OptimizationRunStatus,
    RunStatus,
    RunStepType,
    RunType,
    SandboxProvider,
    TenantStatus,
)


@dataclass(frozen=True)
class IdentityUpsertResult:
    tenant_id: uuid.UUID
    user_id: uuid.UUID | None = None
    tenant_status: TenantStatus | None = None
    membership_role: MembershipRole | None = None
    workspace_id: uuid.UUID | None = None


@dataclass(frozen=True)
class RunCreateRequest:
    tenant_id: uuid.UUID
    created_by_user_id: uuid.UUID | None
    external_run_id: str
    workspace_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = None
    run_type: RunType = RunType.CHAT_TURN
    status: RunStatus = RunStatus.RUNNING
    model_provider: str | None = None
    model_name: str | None = None
    sandbox_provider: SandboxProvider | None = None
    sandbox_session_id: uuid.UUID | None = None
    parent_run_id: uuid.UUID | None = None
    error_json: dict[str, Any] | None = None
    metrics_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunStepCreateRequest:
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    step_index: int
    step_type: RunStepType
    workspace_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = None
    tool_name: str | None = None
    input_json: dict[str, Any] | None = None
    output_json: dict[str, Any] | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    cost_usd_micros: int | None = None


@dataclass(frozen=True)
class ArtifactCreateRequest:
    tenant_id: uuid.UUID
    kind: ArtifactKind
    uri: str
    workspace_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    step_id: uuid.UUID | None = None
    event_id: uuid.UUID | None = None
    provider: ArtifactProvider = ArtifactProvider.MEMORY
    path: str | None = None
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
    workspace_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    uri: str | None = None
    content_text: str | None = None
    content_json: dict[str, Any] | None = None
    importance: int = 0
    tags: list[str] = field(default_factory=list)
    provenance_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JobCreateRequest:
    tenant_id: uuid.UUID
    job_type: JobType
    idempotency_key: str
    workspace_id: uuid.UUID | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    status: JobStatus = JobStatus.QUEUED
    max_attempts: int = 5
    available_at: datetime | None = None


@dataclass(frozen=True)
class JobLeaseRequest:
    tenant_id: uuid.UUID
    worker_id: str
    workspace_id: uuid.UUID | None = None
    limit: int = 1
    available_before: datetime | None = None
    job_type: JobType | None = None
    lease_timeout_seconds: int = 300


@dataclass(frozen=True)
class ChatSessionUpsertRequest:
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID | None
    title: str
    status: ChatSessionStatus = ChatSessionStatus.ACTIVE
    model_provider: str | None = None
    model_name: str | None = None
    active_manifest_path: str | None = None
    session_id: uuid.UUID | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatTurnCreateRequest:
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    session_id: uuid.UUID
    user_message: str
    user_id: uuid.UUID | None = None
    assistant_message: str | None = None
    status: ChatTurnStatus = ChatTurnStatus.COMPLETED
    degraded: bool = False
    model_provider: str | None = None
    model_name: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    error_json: dict[str, Any] | None = None
    run_id: uuid.UUID | None = None


@dataclass(frozen=True)
class DatasetCreateRequest:
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    format: DatasetFormat
    row_count: int
    source: DatasetSource
    module_slug: str | None = None
    uri: str | None = None
    created_by_user_id: uuid.UUID | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OptimizationRunCreateRequest:
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    optimizer: str
    program_spec: str
    status: OptimizationRunStatus = OptimizationRunStatus.RUNNING
    module_slug: str | None = None
    dataset_id: uuid.UUID | None = None
    auto: str | None = None
    train_ratio: float = 0.8
    output_path: str | None = None
    manifest_path: str | None = None
    created_by_user_id: uuid.UUID | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)
