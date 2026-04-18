"""Tenant, user, and workspace identity models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .models_base import Base, _pg_enum
from .models_enums import (
    MembershipRole,
    TenantPlan,
    TenantStatus,
    WorkspaceRole,
    WorkspaceStatus,
)


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_tenants_slug"),
        Index("ix_tenants_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    entra_tenant_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    slug: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
        Index("ix_users_tenant_email", "tenant_id", "email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
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
    __tablename__ = "tenant_memberships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="CASCADE",
            name="fk_tenant_memberships_tenant_user__users_tenant_id_id",
        ),
        UniqueConstraint(
            "tenant_id", "user_id", name="uq_tenant_memberships_tenant_user"
        ),
        Index("ix_tenant_memberships_tenant_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
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


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_workspaces_created_by_user_id__users_id",
        ),
        UniqueConstraint("tenant_id", "slug", name="uq_workspaces_tenant_slug"),
        UniqueConstraint("tenant_id", "id", name="uq_workspaces_tenant_id_id"),
        Index("ix_workspaces_tenant_status", "tenant_id", "status"),
        Index("ix_workspaces_tenant_updated_at", "tenant_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[WorkspaceStatus] = mapped_column(
        _pg_enum(WorkspaceStatus, name="workspace_status"),
        nullable=False,
        server_default=WorkspaceStatus.ACTIVE.value,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    runtime_provider: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'daytona'")
    )
    active_volume_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
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


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_ws_memberships_tenant_workspace",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="CASCADE",
            name="fk_workspace_memberships_tenant_user__users_tenant_id_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_workspace_memberships_workspace_user",
        ),
        Index(
            "ix_workspace_memberships_tenant_workspace",
            "tenant_id",
            "workspace_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[WorkspaceRole] = mapped_column(
        _pg_enum(WorkspaceRole, name="workspace_role"),
        nullable=False,
        server_default=WorkspaceRole.MEMBER.value,
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


class WorkspaceRuntimeSetting(Base):
    __tablename__ = "workspace_runtime_settings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_ws_runtime_settings_tenant_workspace",
        ),
        ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_workspace_runtime_settings_updated_by_user_id__users_id",
        ),
        UniqueConstraint(
            "workspace_id",
            name="uq_workspace_runtime_settings_workspace_id",
        ),
        Index(
            "ix_workspace_runtime_settings_tenant_workspace",
            "tenant_id",
            "workspace_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    settings_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
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
