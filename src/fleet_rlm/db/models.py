"""SQLAlchemy models for fleet-rlm Neon Postgres persistence."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all ORM models."""


def _pg_enum(enum_cls: type[enum.Enum], *, name: str) -> Enum:
    """Bind Python enums to existing Postgres enum values (not member names)."""
    return Enum(
        enum_cls,
        name=name,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
        native_enum=True,
    )


class TenantPlan(str, enum.Enum):
    FREE = "free"
    TEAM = "team"
    ENTERPRISE = "enterprise"


class TenantStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class MembershipRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class SandboxProvider(str, enum.Enum):
    MODAL = "modal"
    DAYTONA = "daytona"
    ACA_JOBS = "aca_jobs"
    LOCAL = "local"


class SandboxSessionStatus(str, enum.Enum):
    ACTIVE = "active"
    ENDED = "ended"
    FAILED = "failed"


class RunStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunStepType(str, enum.Enum):
    TOOL_CALL = "tool_call"
    REPL_EXEC = "repl_exec"
    LLM_CALL = "llm_call"
    RETRIEVAL = "retrieval"
    GUARDRAIL = "guardrail"
    SUMMARY = "summary"
    MEMORY = "memory"
    OUTPUT = "output"
    STATUS = "status"


class JobType(str, enum.Enum):
    RUN_TASK = "run_task"
    MEMORY_COMPACTION = "memory_compaction"
    EVALUATION = "evaluation"
    MAINTENANCE = "maintenance"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"


class MemoryScope(str, enum.Enum):
    USER = "user"
    TENANT = "tenant"
    RUN = "run"
    AGENT = "agent"


class MemoryKind(str, enum.Enum):
    NOTE = "note"
    SUMMARY = "summary"
    FACT = "fact"
    PREFERENCE = "preference"
    CONTEXT = "context"


class MemorySource(str, enum.Enum):
    USER_INPUT = "user_input"
    SYSTEM = "system"
    TOOL = "tool"
    LLM = "llm"
    IMPORTED = "imported"


class ArtifactKind(str, enum.Enum):
    FILE = "file"
    LOG = "log"
    REPORT = "report"
    TRACE = "trace"
    IMAGE = "image"
    DATA = "data"


class BillingSource(str, enum.Enum):
    AZURE_MARKETPLACE = "azure_marketplace"
    MANUAL = "manual"


class SubscriptionStatus(str, enum.Enum):
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    entra_tenant_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plan: Mapped[TenantPlan] = mapped_column(
        _pg_enum(TenantPlan, name="tenant_plan"),
        nullable=False,
        server_default=TenantPlan.FREE.value,
    )
    status: Mapped[TenantStatus] = mapped_column(
        _pg_enum(TenantStatus, name="tenant_status"),
        nullable=False,
        server_default=TenantStatus.ACTIVE.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "entra_user_id", name="uq_users_tenant_entra_user"
        ),
        UniqueConstraint("tenant_id", "id", name="uq_users_tenant_id_id"),
        Index("ix_users_tenant_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    entra_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="CASCADE",
            name="fk_memberships_tenant_user__users_tenant_id_id",
        ),
        UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),
        Index("ix_memberships_tenant_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[MembershipRole] = mapped_column(
        _pg_enum(MembershipRole, name="membership_role"),
        nullable=False,
        server_default=MembershipRole.MEMBER.value,
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SandboxSession(Base):
    __tablename__ = "sandbox_sessions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sandbox_sessions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "provider",
            "external_id",
            name="uq_sandbox_sessions_tenant_provider_external",
        ),
        Index("ix_sandbox_sessions_tenant_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[SandboxProvider] = mapped_column(
        _pg_enum(SandboxProvider, name="sandbox_provider"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[SandboxSessionStatus] = mapped_column(
        _pg_enum(SandboxSessionStatus, name="sandbox_session_status"),
        nullable=False,
        server_default=SandboxSessionStatus.ACTIVE.value,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ModalVolume(Base):
    __tablename__ = "modal_volumes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_modal_volumes_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "volume_name", name="uq_modal_volumes_tenant_volume_name"
        ),
        Index("ix_modal_volumes_tenant_created_at", "tenant_id", "created_at"),
        Index("ix_modal_volumes_tenant_last_seen_at", "tenant_id", "last_seen_at"),
        Index(
            "ix_modal_volumes_tenant_provider_env_last_seen",
            "tenant_id",
            "provider",
            "environment",
            "last_seen_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'modal'")
    )
    volume_name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_volume_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    environment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_runs_tenant_created_by_user__users_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "sandbox_session_id"],
            ["sandbox_sessions.tenant_id", "sandbox_sessions.id"],
            ondelete="SET NULL",
            name="fk_runs_tenant_sandbox_session__sandbox_sessions_tenant_id_id",
        ),
        UniqueConstraint(
            "tenant_id", "external_run_id", name="uq_runs_tenant_external_run"
        ),
        UniqueConstraint("tenant_id", "id", name="uq_runs_tenant_id_id"),
        Index("ix_runs_tenant_created_at", "tenant_id", "created_at"),
        Index("ix_runs_tenant_status_created_at", "tenant_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    external_run_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    status: Mapped[RunStatus] = mapped_column(
        _pg_enum(RunStatus, name="run_status"),
        nullable=False,
        server_default=RunStatus.RUNNING.value,
    )
    model_provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sandbox_provider: Mapped[SandboxProvider | None] = mapped_column(
        _pg_enum(SandboxProvider, name="sandbox_provider"), nullable=True
    )
    sandbox_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    error_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class RunStep(Base):
    __tablename__ = "run_steps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["runs.tenant_id", "runs.id"],
            ondelete="CASCADE",
            name="fk_run_steps_tenant_run__runs_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "modal_volume_id"],
            ["modal_volumes.tenant_id", "modal_volumes.id"],
            ondelete="SET NULL",
            name="fk_run_steps_tenant_modal_volume__modal_volumes_tenant_id_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "run_id",
            "step_index",
            name="uq_run_steps_tenant_run_step_index",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_run_steps_tenant_id_id"),
        Index("ix_run_steps_tenant_created_at", "tenant_id", "created_at"),
        Index("ix_run_steps_run_id", "run_id"),
        Index("ix_run_steps_tenant_run_step", "tenant_id", "run_id", "step_index"),
        Index(
            "ix_run_steps_tenant_type_created_at",
            "tenant_id",
            "step_type",
            "created_at",
        ),
        Index(
            "ix_run_steps_tenant_modal_volume_created_at",
            "tenant_id",
            "modal_volume_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[RunStepType] = mapped_column(
        _pg_enum(RunStepType, name="run_step_type"), nullable=False
    )
    input_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sandbox_session_external_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    modal_volume_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    modal_volume_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cost_usd_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["runs.tenant_id", "runs.id"],
            ondelete="CASCADE",
            name="fk_artifacts_tenant_run__runs_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "step_id"],
            ["run_steps.tenant_id", "run_steps.id"],
            ondelete="SET NULL",
            name="fk_artifacts_tenant_step__run_steps_tenant_id_id",
        ),
        Index("ix_artifacts_tenant_created_at", "tenant_id", "created_at"),
        Index(
            "ix_artifacts_tenant_run_created_at", "tenant_id", "run_id", "created_at"
        ),
        Index("ix_artifacts_tenant_kind_created_at", "tenant_id", "kind", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    kind: Mapped[ArtifactKind] = mapped_column(
        _pg_enum(ArtifactKind, name="artifact_kind"), nullable=False
    )
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RLMProgram(Base):
    __tablename__ = "rlm_programs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_rlm_programs_tenant_id_id"),
        UniqueConstraint("tenant_id", "program_key", name="uq_rlm_programs_tenant_key"),
        Index("ix_rlm_programs_tenant_created_at", "tenant_id", "created_at"),
        Index("ix_rlm_programs_tenant_kind_status", "tenant_id", "kind", "status"),
        Index("ix_rlm_programs_tenant_updated_at", "tenant_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    program_key: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kind: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'compiled'")
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'active'")
    )
    dspy_signature: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version_tag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    program_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class RLMTrace(Base):
    __tablename__ = "rlm_traces"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["runs.tenant_id", "runs.id"],
            ondelete="CASCADE",
            name="fk_rlm_traces_tenant_run__runs_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_step_id"],
            ["run_steps.tenant_id", "run_steps.id"],
            ondelete="SET NULL",
            name="fk_rlm_traces_tenant_step__run_steps_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "program_id"],
            ["rlm_programs.tenant_id", "rlm_programs.id"],
            ondelete="SET NULL",
            name="fk_rlm_traces_tenant_program__rlm_programs_tenant_id_id",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_rlm_traces_tenant_id_id"),
        Index("ix_rlm_traces_tenant_created_at", "tenant_id", "created_at"),
        Index(
            "ix_rlm_traces_tenant_run_created_at", "tenant_id", "run_id", "created_at"
        ),
        Index("ix_rlm_traces_tenant_kind_status", "tenant_id", "trace_kind", "status"),
        Index(
            "ix_rlm_traces_tenant_program_created_at",
            "tenant_id",
            "program_id",
            "created_at",
        ),
        Index(
            "ix_rlm_traces_tenant_step_created_at",
            "tenant_id",
            "run_step_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    program_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    trace_kind: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'trajectory'")
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'captured'")
    )
    source: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'rlm'")
    )
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class MemoryItem(Base):
    __tablename__ = "memory_items"
    __table_args__ = (
        CheckConstraint(
            "importance >= 0 AND importance <= 100",
            name="ck_memory_items_importance_range",
        ),
        Index("ix_memory_items_tenant_created_at", "tenant_id", "created_at"),
        Index("ix_memory_items_scope", "tenant_id", "scope", "scope_id", "created_at"),
        Index("ix_memory_items_tags", "tags", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[MemoryScope] = mapped_column(
        _pg_enum(MemoryScope, name="memory_scope"), nullable=False
    )
    scope_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[MemoryKind] = mapped_column(
        _pg_enum(MemoryKind, name="memory_kind"), nullable=False
    )
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source: Mapped[MemorySource] = mapped_column(
        _pg_enum(MemorySource, name="memory_source"), nullable=False
    )
    importance: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_jobs_tenant_idempotency_key"
        ),
        Index("ix_jobs_status_available_at", "status", "available_at"),
        Index("ix_jobs_tenant_status_available", "tenant_id", "status", "available_at"),
        Index("ix_jobs_tenant_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_type: Mapped[JobType] = mapped_column(
        _pg_enum(JobType, name="job_type"), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        _pg_enum(JobStatus, name="job_status"),
        nullable=False,
        server_default=JobStatus.QUEUED.value,
    )
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("5")
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    locked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    last_error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TenantSubscription(Base):
    __tablename__ = "tenant_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "billing_source",
            "subscription_id",
            name="uq_tenant_subscriptions_source_subscription",
        ),
        Index("ix_tenant_subscriptions_tenant_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    billing_source: Mapped[BillingSource] = mapped_column(
        _pg_enum(BillingSource, name="billing_source"),
        nullable=False,
        server_default=BillingSource.MANUAL.value,
    )
    subscription_id: Mapped[str] = mapped_column(String(255), nullable=False)
    offer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[SubscriptionStatus] = mapped_column(
        _pg_enum(SubscriptionStatus, name="subscription_status"),
        nullable=False,
        server_default=SubscriptionStatus.ACTIVE.value,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
