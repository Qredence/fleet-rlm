"""Runtime, artifact, and trace persistence models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
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
from .models_enums import (
    ArtifactKind,
    ArtifactProvider,
    ChatSessionStatus,
    ChatTurnStatus,
    ExternalTraceProvider,
    RunStatus,
    RunStepType,
    RunType,
    SandboxProvider,
)


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_chat_sessions_tenant_workspace__workspaces_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_chat_sessions_user_id__users_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="uq_chat_sessions_tenant_workspace_id",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_chat_sessions_tenant_id_id"),
        Index("ix_chat_sessions_workspace_updated_at", "workspace_id", "updated_at"),
        Index("ix_chat_sessions_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    title: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=text("'New Session'")
    )
    status: Mapped[ChatSessionStatus] = mapped_column(
        _pg_enum(ChatSessionStatus, name="chat_session_status"),
        nullable=False,
        server_default=ChatSessionStatus.ACTIVE.value,
    )
    model_provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active_manifest_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    monotonic_turn_counter: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    latest_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class ChatTurn(Base):
    __tablename__ = "chat_turns"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_chat_turns_tenant_workspace__workspaces_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "session_id"],
            [
                "chat_sessions.tenant_id",
                "chat_sessions.workspace_id",
                "chat_sessions.id",
            ],
            ondelete="CASCADE",
            name="fk_chat_turns_tenant_workspace_session__chat_sessions",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_chat_turns_user_id__users_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="uq_chat_turns_tenant_workspace_id",
        ),
        UniqueConstraint(
            "session_id",
            "turn_index",
            name="uq_chat_turns_session_turn_index",
        ),
        Index("ix_chat_turns_session_created_at", "session_id", "created_at"),
        Index("ix_chat_turns_workspace_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ChatTurnStatus] = mapped_column(
        _pg_enum(ChatTurnStatus, name="chat_turn_status"),
        nullable=False,
        server_default=ChatTurnStatus.COMPLETED.value,
    )
    degraded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    model_provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Run(Base):
    __tablename__ = "execution_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_execution_runs_tenant_workspace__workspaces_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_execution_runs_created_by_user_id__users_id",
        ),
        ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            ondelete="SET NULL",
            name="fk_execution_runs_session_id__chat_sessions_id",
        ),
        ForeignKeyConstraint(
            ["turn_id"],
            ["chat_turns.id"],
            ondelete="SET NULL",
            name="fk_execution_runs_turn_id__chat_turns_id",
        ),
        ForeignKeyConstraint(
            ["sandbox_session_id"],
            ["sandbox_sessions.id"],
            ondelete="SET NULL",
            name="fk_execution_runs_sandbox_session_id__sandbox_sessions_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="uq_execution_runs_tenant_workspace_id",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_execution_runs_tenant_id_id"),
        UniqueConstraint(
            "workspace_id",
            "external_run_id",
            name="uq_execution_runs_workspace_external_run",
        ),
        Index("ix_execution_runs_workspace_created_at", "workspace_id", "created_at"),
        Index(
            "ix_execution_runs_workspace_status_created_at",
            "workspace_id",
            "status",
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
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("execution_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    external_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    run_type: Mapped[RunType] = mapped_column(
        _pg_enum(RunType, name="run_type"),
        nullable=False,
        server_default=RunType.CHAT_TURN.value,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    status: Mapped[RunStatus] = mapped_column(
        _pg_enum(RunStatus, name="run_status"),
        nullable=False,
        server_default=RunStatus.RUNNING.value,
    )
    model_provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sandbox_provider: Mapped[SandboxProvider | None] = mapped_column(
        _pg_enum(SandboxProvider, name="sandbox_provider"),
        nullable=True,
    )
    sandbox_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    error_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    metrics_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class RunStep(Base):
    __tablename__ = "execution_steps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_execution_steps_tenant_workspace__workspaces_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "run_id"],
            [
                "execution_runs.tenant_id",
                "execution_runs.workspace_id",
                "execution_runs.id",
            ],
            ondelete="CASCADE",
            name="fk_execution_steps_tenant_workspace_run__execution_runs",
        ),
        ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            ondelete="SET NULL",
            name="fk_execution_steps_session_id__chat_sessions_id",
        ),
        ForeignKeyConstraint(
            ["turn_id"],
            ["chat_turns.id"],
            ondelete="SET NULL",
            name="fk_execution_steps_turn_id__chat_turns_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="uq_execution_steps_tenant_workspace_id",
        ),
        UniqueConstraint(
            "run_id", "step_index", name="uq_execution_steps_run_step_index"
        ),
        Index("ix_execution_steps_run_step", "run_id", "step_index"),
        Index(
            "ix_execution_steps_workspace_type_created_at",
            "workspace_id",
            "step_type",
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
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[RunStepType] = mapped_column(
        _pg_enum(RunStepType, name="run_step_type"), nullable=False
    )
    tool_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
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


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_execution_events_tenant_workspace__workspaces_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "run_id"],
            [
                "execution_runs.tenant_id",
                "execution_runs.workspace_id",
                "execution_runs.id",
            ],
            ondelete="CASCADE",
            name="fk_execution_events_tenant_workspace_run__execution_runs",
        ),
        ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            ondelete="SET NULL",
            name="fk_execution_events_session_id__chat_sessions_id",
        ),
        ForeignKeyConstraint(
            ["turn_id"],
            ["chat_turns.id"],
            ondelete="SET NULL",
            name="fk_execution_events_turn_id__chat_turns_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="uq_execution_events_tenant_workspace_id",
        ),
        UniqueConstraint("run_id", "sequence", name="uq_execution_events_run_sequence"),
        Index("ix_execution_events_run_sequence", "run_id", "sequence"),
        Index("ix_execution_events_workspace_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SessionStateSnapshot(Base):
    __tablename__ = "session_state_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_state_snapshots_tenant_workspace",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "session_id"],
            [
                "chat_sessions.tenant_id",
                "chat_sessions.workspace_id",
                "chat_sessions.id",
            ],
            ondelete="CASCADE",
            name="fk_state_snapshots_tenant_workspace_session",
        ),
        UniqueConstraint(
            "session_id",
            "revision",
            name="uq_session_state_snapshots_session_revision",
        ),
        Index(
            "ix_session_state_snapshots_session_created_at",
            "session_id",
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
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    manifest_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_artifacts_tenant_workspace__workspaces_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["run_id"],
            ["execution_runs.id"],
            ondelete="SET NULL",
            name="fk_artifacts_run_id__execution_runs_id",
        ),
        ForeignKeyConstraint(
            ["step_id"],
            ["execution_steps.id"],
            ondelete="SET NULL",
            name="fk_artifacts_step_id__execution_steps_id",
        ),
        ForeignKeyConstraint(
            ["event_id"],
            ["execution_events.id"],
            ondelete="SET NULL",
            name="fk_artifacts_event_id__execution_events_id",
        ),
        ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            ondelete="SET NULL",
            name="fk_artifacts_session_id__chat_sessions_id",
        ),
        ForeignKeyConstraint(
            ["turn_id"],
            ["chat_turns.id"],
            ondelete="SET NULL",
            name="fk_artifacts_turn_id__chat_turns_id",
        ),
        Index("ix_artifacts_workspace_created_at", "workspace_id", "created_at"),
        Index(
            "ix_artifacts_workspace_kind_created_at",
            "workspace_id",
            "kind",
            "created_at",
        ),
        Index(
            "ix_artifacts_workspace_run_created_at",
            "workspace_id",
            "run_id",
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
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    kind: Mapped[ArtifactKind] = mapped_column(
        _pg_enum(ArtifactKind, name="artifact_kind"), nullable=False
    )
    provider: Mapped[ArtifactProvider] = mapped_column(
        _pg_enum(ArtifactProvider, name="artifact_provider"),
        nullable=False,
        server_default=ArtifactProvider.MEMORY.value,
    )
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    __tablename__ = "program_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_program_versions_tenant_workspace__workspaces_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["source_run_id"],
            ["execution_runs.id"],
            ondelete="SET NULL",
            name="fk_program_versions_source_run_id__execution_runs_id",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_program_versions_tenant_id_id"),
        UniqueConstraint(
            "workspace_id",
            "program_key",
            "version_tag",
            name="uq_program_versions_workspace_key_tag",
        ),
        Index("ix_program_versions_workspace_created_at", "workspace_id", "created_at"),
        Index("ix_program_versions_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
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


class ExternalTrace(Base):
    __tablename__ = "external_traces"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_external_traces_tenant_workspace__workspaces_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["run_id"],
            ["execution_runs.id"],
            ondelete="SET NULL",
            name="fk_external_traces_run_id__execution_runs_id",
        ),
        ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            ondelete="SET NULL",
            name="fk_external_traces_session_id__chat_sessions_id",
        ),
        ForeignKeyConstraint(
            ["turn_id"],
            ["chat_turns.id"],
            ondelete="SET NULL",
            name="fk_external_traces_turn_id__chat_turns_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "provider",
            "trace_id",
            name="uq_external_traces_tenant_provider_trace_id",
        ),
        Index("ix_external_traces_client_request_id", "client_request_id"),
        Index(
            "ix_external_traces_workspace_observed_at", "workspace_id", "observed_at"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    provider: Mapped[ExternalTraceProvider] = mapped_column(
        _pg_enum(ExternalTraceProvider, name="external_trace_provider"),
        nullable=False,
        server_default=ExternalTraceProvider.MLFLOW.value,
    )
    trace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    client_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    experiment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    experiment_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    observed_at: Mapped[datetime] = mapped_column(
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


class TraceFeedback(Base):
    __tablename__ = "trace_feedback"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_trace_feedback_tenant_workspace__workspaces_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["reviewer_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_trace_feedback_reviewer_user_id__users_id",
        ),
        ForeignKeyConstraint(
            ["external_trace_id"],
            ["external_traces.id"],
            ondelete="CASCADE",
            name="fk_trace_feedback_external_trace_id__external_traces_id",
        ),
        Index(
            "ix_trace_feedback_external_trace_created_at",
            "external_trace_id",
            "created_at",
        ),
        Index("ix_trace_feedback_workspace_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    external_trace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# Compatibility alias retained for existing imports.
RLMTrace = ExternalTrace
