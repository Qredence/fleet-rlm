"""Sandbox and volume persistence models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
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
from .models_enums import SandboxProvider, SandboxSessionStatus


class SandboxSession(Base):
    __tablename__ = "sandbox_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_sandbox_sessions_tenant_user__users_tenant_id_id",
        ),
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
