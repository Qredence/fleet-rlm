"""Job, sandbox, and subscription persistence models (ops domain)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from fleet_rlm.db.base import Base, _pg_enum
from fleet_rlm.db.enums import (
    BillingSource,
    JobStatus,
    JobType,
    SandboxProvider,
    SandboxSessionStatus,
    SubscriptionStatus,
)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_jobs_tenant_workspace__workspaces_tenant_id_id",
        ),
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_jobs_workspace_idempotency_key"),
        Index("ix_jobs_status_available_at", "status", "available_at"),
        Index(
            "ix_jobs_workspace_status_available",
            "workspace_id",
            "status",
            "available_at",
        ),
        Index("ix_jobs_workspace_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    job_type: Mapped[JobType] = mapped_column(_pg_enum(JobType, name="job_type"), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        _pg_enum(JobStatus, name="job_status"),
        nullable=False,
        server_default=JobStatus.QUEUED.value,
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    last_error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
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
        Index("ix_tenant_subscriptions_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    billing_source: Mapped[BillingSource] = mapped_column(
        _pg_enum(BillingSource, name="billing_source"),
        nullable=False,
        server_default=BillingSource.MANUAL.value,
    )
    purchaser_tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    subscription_id: Mapped[str] = mapped_column(String(255), nullable=False)
    offer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[SubscriptionStatus] = mapped_column(
        _pg_enum(SubscriptionStatus, name="subscription_status"),
        nullable=False,
        server_default=SubscriptionStatus.ACTIVE.value,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SandboxSession(Base):
    __tablename__ = "sandbox_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_sandbox_sessions_tenant_workspace__workspaces_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_sandbox_sessions_created_by_user_id__users_id",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_sandbox_sessions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="uq_sandbox_sessions_tenant_workspace_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "provider",
            "external_id",
            name="uq_sandbox_sessions_workspace_provider_external",
        ),
        Index("ix_sandbox_sessions_workspace_created_at", "workspace_id", "created_at"),
        Index("ix_sandbox_sessions_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    provider: Mapped[SandboxProvider] = mapped_column(
        _pg_enum(SandboxProvider, name="sandbox_provider"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[SandboxSessionStatus] = mapped_column(
        _pg_enum(SandboxSessionStatus, name="sandbox_session_status"),
        nullable=False,
        server_default=SandboxSessionStatus.ACTIVE.value,
    )
    volume_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    volume_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    diagnostics_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
