"""Run, artifact, and RLM trace models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .models_base import Base, _pg_enum
from .models_enums import ArtifactKind, RunStatus, RunStepType, SandboxProvider


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
