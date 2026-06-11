"""LLM provider profile persistence models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .models_base import Base


class LlmProviderProfile(Base):
    __tablename__ = "llm_provider_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()"))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(64), nullable=False)
    api_base: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class LlmRoleBinding(Base):
    __tablename__ = "llm_role_bindings"

    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_provider_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    model_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
