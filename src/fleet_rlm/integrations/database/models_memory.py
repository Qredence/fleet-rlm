"""Memory persistence models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .models_base import Base, _pg_enum
from .models_enums import MemoryKind, MemoryScope, MemorySource, MemoryStatus


class MemoryItem(Base):
    __tablename__ = "memory_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_memory_items_tenant_workspace__workspaces_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_memory_items_user_id__users_id",
        ),
        CheckConstraint(
            "importance >= 0 AND importance <= 100",
            name="ck_memory_items_importance_range",
        ),
        Index("ix_memory_items_workspace_created_at", "workspace_id", "created_at"),
        Index(
            "ix_memory_items_workspace_scope",
            "workspace_id",
            "scope",
            "scope_id",
            "created_at",
        ),
        Index("ix_memory_items_status", "workspace_id", "status", "created_at"),
        Index("ix_memory_items_tags", "tags", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    scope: Mapped[MemoryScope] = mapped_column(
        _pg_enum(MemoryScope, name="memory_scope"), nullable=False
    )
    scope_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[MemoryKind] = mapped_column(
        _pg_enum(MemoryKind, name="memory_kind"), nullable=False
    )
    status: Mapped[MemoryStatus] = mapped_column(
        _pg_enum(MemoryStatus, name="memory_status"),
        nullable=False,
        server_default=MemoryStatus.ACTIVE.value,
    )
    uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    provenance_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
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


class MemoryLink(Base):
    __tablename__ = "memory_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_memory_links_tenant_workspace__workspaces_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["source_memory_id"],
            ["memory_items.id"],
            ondelete="CASCADE",
            name="fk_memory_links_source_memory_id__memory_items_id",
        ),
        UniqueConstraint(
            "source_memory_id",
            "target_kind",
            "target_id",
            "link_type",
            name="uq_memory_links_source_target_type",
        ),
        Index("ix_memory_links_workspace_created_at", "workspace_id", "created_at"),
        Index(
            "ix_memory_links_target_lookup",
            "workspace_id",
            "target_kind",
            "target_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_memory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    target_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    link_type: Mapped[str] = mapped_column(String(128), nullable=False)
    weight: Mapped[float] = mapped_column(nullable=False, server_default=text("1.0"))
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
