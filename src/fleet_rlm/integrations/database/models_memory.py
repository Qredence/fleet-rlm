"""Memory persistence models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .models_base import Base, _pg_enum
from .models_enums import MemoryKind, MemoryScope, MemorySource


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
    uri: Mapped[str | None] = mapped_column(Text, nullable=True)
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
