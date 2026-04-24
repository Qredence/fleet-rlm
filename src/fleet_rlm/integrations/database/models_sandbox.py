"""Sandbox and volume persistence models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .models_base import Base, _pg_enum
from .models_enums import (
    SandboxProvider,
    SandboxSessionStatus,
    VolumeObjectType,
    WorkspaceVolumeStatus,
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

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
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
    volume_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    volume_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    diagnostics_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
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


class WorkspaceVolume(Base):
    __tablename__ = "workspace_volumes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_workspace_volumes_tenant_workspace__workspaces_tenant_id_id",
        ),
        UniqueConstraint(
            "workspace_id",
            "external_volume_id",
            name="uq_workspace_volumes_workspace_external_id",
        ),
        Index("ix_workspace_volumes_workspace_status", "workspace_id", "status"),
        Index(
            "ix_workspace_volumes_workspace_updated_at", "workspace_id", "updated_at"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[SandboxProvider] = mapped_column(
        _pg_enum(SandboxProvider, name="sandbox_provider"), nullable=False
    )
    external_volume_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_volume_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mount_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[WorkspaceVolumeStatus] = mapped_column(
        _pg_enum(WorkspaceVolumeStatus, name="workspace_volume_status"),
        nullable=False,
        server_default=WorkspaceVolumeStatus.PROVISIONING.value,
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


class VolumeObject(Base):
    __tablename__ = "volume_objects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_volume_objects_tenant_workspace__workspaces_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["workspace_volume_id"],
            ["workspace_volumes.id"],
            ondelete="CASCADE",
            name="fk_volume_objects_workspace_volume_id__workspace_volumes_id",
        ),
        UniqueConstraint(
            "workspace_volume_id",
            "path",
            name="uq_volume_objects_workspace_volume_path",
        ),
        Index(
            "ix_volume_objects_workspace_volume_modified",
            "workspace_volume_id",
            "modified_at",
        ),
        Index("ix_volume_objects_workspace_path", "workspace_id", "path"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_volume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    object_type: Mapped[VolumeObjectType] = mapped_column(
        _pg_enum(VolumeObjectType, name="volume_object_type"), nullable=False
    )
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(255), nullable=True)
    modified_at: Mapped[datetime | None] = mapped_column(
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
