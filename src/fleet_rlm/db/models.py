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
    Numeric,
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


class SkillSource(str, enum.Enum):
    SCAFFOLD = "scaffold"
    IMPORTED = "imported"
    USER_DEFINED = "user_defined"
    SYSTEM = "system"


class SkillStatus(str, enum.Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"


class SkillLinkSource(str, enum.Enum):
    MANUAL = "manual"
    INFERRED = "inferred"
    IMPORTED = "imported"


class SkillUsageStatus(str, enum.Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


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


class SkillTaxonomy(Base):
    __tablename__ = "skill_taxonomies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL",
            name="fk_skill_taxonomies_tenant_created_by_user__users_tenant_id_id",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_skill_taxonomies_tenant_id_id"),
        UniqueConstraint("tenant_id", "key", name="uq_skill_taxonomies_tenant_key"),
        Index("ix_skill_taxonomies_tenant_created_at", "tenant_id", "created_at"),
        Index("ix_skill_taxonomies_tenant_key", "tenant_id", "key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
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


class TaxonomyTerm(Base):
    __tablename__ = "taxonomy_terms"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "taxonomy_id"],
            ["skill_taxonomies.tenant_id", "skill_taxonomies.id"],
            ondelete="CASCADE",
            name="fk_tax_terms_tenant_taxonomy",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "parent_term_id"],
            ["taxonomy_terms.tenant_id", "taxonomy_terms.id"],
            ondelete="SET NULL",
            name="fk_taxonomy_terms_tenant_parent__taxonomy_terms_tenant_id_id",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_taxonomy_terms_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "taxonomy_id",
            "slug",
            name="uq_taxonomy_terms_tenant_taxonomy_slug",
        ),
        CheckConstraint(
            "id <> parent_term_id", name="ck_taxonomy_terms_parent_not_self"
        ),
        Index(
            "ix_taxonomy_terms_tenant_taxonomy_parent_sort",
            "tenant_id",
            "taxonomy_id",
            "parent_term_id",
            "sort_order",
        ),
        Index("ix_taxonomy_terms_tenant_slug", "tenant_id", "slug"),
        Index("ix_taxonomy_terms_synonyms", "synonyms", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    taxonomy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    parent_term_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    synonyms: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
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


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL",
            name="fk_skills_tenant_created_by_user__users_tenant_id_id",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_skills_tenant_id_id"),
        UniqueConstraint("tenant_id", "stable_key", name="uq_skills_tenant_stable_key"),
        Index("ix_skills_tenant_created_at", "tenant_id", "created_at"),
        Index("ix_skills_tenant_status", "tenant_id", "status"),
        Index("ix_skills_tenant_display_name", "tenant_id", "display_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[SkillSource] = mapped_column(
        _pg_enum(SkillSource, name="skill_source"),
        nullable=False,
        server_default=SkillSource.SCAFFOLD.value,
    )
    status: Mapped[SkillStatus] = mapped_column(
        _pg_enum(SkillStatus, name="skill_status"),
        nullable=False,
        server_default=SkillStatus.ACTIVE.value,
    )
    latest_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
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


class SkillVersion(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "skill_id"],
            ["skills.tenant_id", "skills.id"],
            ondelete="CASCADE",
            name="fk_skill_versions_tenant_skill__skills_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL",
            name="fk_skill_versions_tenant_created_by_user__users_tenant_id_id",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_skill_versions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "skill_id",
            "version_num",
            name="uq_skill_versions_tenant_skill_version_num",
        ),
        CheckConstraint("version_num > 0", name="ck_skill_versions_version_positive"),
        Index(
            "ix_skill_versions_tenant_skill_version_num",
            "tenant_id",
            "skill_id",
            "version_num",
        ),
        Index(
            "uq_skill_versions_tenant_skill_current",
            "tenant_id",
            "skill_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_num: Mapped[int] = mapped_column(Integer, nullable=False)
    semver: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    checksum: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SkillTermLink(Base):
    __tablename__ = "skill_term_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "skill_id"],
            ["skills.tenant_id", "skills.id"],
            ondelete="CASCADE",
            name="fk_skill_term_links_tenant_skill__skills_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "term_id"],
            ["taxonomy_terms.tenant_id", "taxonomy_terms.id"],
            ondelete="CASCADE",
            name="fk_skill_term_links_tenant_term__taxonomy_terms_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL",
            name="fk_skill_term_links_tenant_created_by_user__users_tenant_id_id",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_skill_term_links_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "skill_id",
            "term_id",
            name="uq_skill_term_links_tenant_skill_term",
        ),
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_skill_term_links_confidence_range",
        ),
        Index("ix_skill_term_links_tenant_skill", "tenant_id", "skill_id"),
        Index("ix_skill_term_links_tenant_term", "tenant_id", "term_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    term_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    confidence: Mapped[float] = mapped_column(
        Numeric(5, 4), nullable=False, server_default=text("1.0")
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    source: Mapped[SkillLinkSource] = mapped_column(
        _pg_enum(SkillLinkSource, name="skill_link_source"),
        nullable=False,
        server_default=SkillLinkSource.MANUAL.value,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RunSkillUsage(Base):
    __tablename__ = "run_skill_usages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["runs.tenant_id", "runs.id"],
            ondelete="CASCADE",
            name="fk_run_skill_usages_tenant_run__runs_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "step_id"],
            ["run_steps.tenant_id", "run_steps.id"],
            ondelete="SET NULL",
            name="fk_run_skill_usages_tenant_step__run_steps_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "skill_id"],
            ["skills.tenant_id", "skills.id"],
            ondelete="RESTRICT",
            name="fk_run_skill_usages_tenant_skill__skills_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "skill_version_id"],
            ["skill_versions.tenant_id", "skill_versions.id"],
            ondelete="SET NULL",
            name="fk_run_skill_usages_tenant_skill_ver",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_run_skill_usages_tenant_id_id"),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_run_skill_usages_time_order",
        ),
        Index(
            "ix_run_skill_usages_tenant_run_started_at",
            "tenant_id",
            "run_id",
            "started_at",
        ),
        Index(
            "ix_run_skill_usages_tenant_skill_started_at",
            "tenant_id",
            "skill_id",
            "started_at",
        ),
        Index("ix_run_skill_usages_tenant_step", "tenant_id", "step_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    skill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    skill_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    status: Mapped[SkillUsageStatus] = mapped_column(
        _pg_enum(SkillUsageStatus, name="skill_usage_status"),
        nullable=False,
        server_default=SkillUsageStatus.STARTED.value,
    )
    invocation_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
