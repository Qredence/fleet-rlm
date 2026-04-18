"""Optimization and dataset persistence models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Float,
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
    DatasetFormat,
    DatasetSource,
    OptimizationRunStatus,
    PromptSnapshotType,
)


class OptimizationModule(Base):
    __tablename__ = "optimization_modules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_opt_modules_tenant_workspace",
        ),
        UniqueConstraint(
            "workspace_id",
            "slug",
            name="uq_optimization_modules_workspace_slug",
        ),
        Index(
            "ix_optimization_modules_workspace_created_at", "workspace_id", "created_at"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_dataset_keys: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    output_key: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default=text("'assistant_response'")
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'active'")
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


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_datasets_tenant_workspace__workspaces_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["optimization_module_id"],
            ["optimization_modules.id"],
            ondelete="SET NULL",
            name="fk_datasets_optimization_module_id__optimization_modules_id",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_datasets_created_by_user_id__users_id",
        ),
        Index("ix_datasets_workspace_created_at", "workspace_id", "created_at"),
        Index(
            "ix_datasets_workspace_module_created_at",
            "workspace_id",
            "optimization_module_id",
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
    optimization_module_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    row_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    format: Mapped[DatasetFormat] = mapped_column(
        _pg_enum(DatasetFormat, name="dataset_format"), nullable=False
    )
    source: Mapped[DatasetSource] = mapped_column(
        _pg_enum(DatasetSource, name="dataset_source"),
        nullable=False,
        server_default=DatasetSource.UPLOAD.value,
    )
    uri: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class DatasetExample(Base):
    __tablename__ = "dataset_examples"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_dataset_examples_tenant_workspace__workspaces_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            ondelete="CASCADE",
            name="fk_dataset_examples_dataset_id__datasets_id",
        ),
        UniqueConstraint(
            "dataset_id", "row_index", name="uq_dataset_examples_dataset_row_index"
        ),
        Index("ix_dataset_examples_dataset_row_index", "dataset_id", "row_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    input_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OptimizationRun(Base):
    __tablename__ = "optimization_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_optimization_runs_tenant_workspace__workspaces_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["optimization_module_id"],
            ["optimization_modules.id"],
            ondelete="SET NULL",
            name="fk_optimization_runs_module_id__optimization_modules_id",
        ),
        ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            ondelete="SET NULL",
            name="fk_optimization_runs_dataset_id__datasets_id",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_optimization_runs_created_by_user_id__users_id",
        ),
        Index(
            "ix_optimization_runs_workspace_created_at", "workspace_id", "created_at"
        ),
        Index("ix_optimization_runs_workspace_status", "workspace_id", "status"),
        CheckConstraint(
            "train_ratio > 0 AND train_ratio < 1",
            name="ck_optimization_runs_train_ratio_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    optimization_module_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    status: Mapped[OptimizationRunStatus] = mapped_column(
        _pg_enum(OptimizationRunStatus, name="optimization_run_status"),
        nullable=False,
        server_default=OptimizationRunStatus.RUNNING.value,
    )
    program_spec: Mapped[str] = mapped_column(String(255), nullable=False)
    optimizer: Mapped[str] = mapped_column(String(64), nullable=False)
    auto: Mapped[str | None] = mapped_column(String(16), nullable=True)
    train_ratio: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0.8")
    )
    train_examples: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_examples: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    phase: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_evaluation_results_tenant_workspace__workspaces_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["optimization_run_id"],
            ["optimization_runs.id"],
            ondelete="CASCADE",
            name="fk_evaluation_results_run_id__optimization_runs_id",
        ),
        ForeignKeyConstraint(
            ["dataset_example_id"],
            ["dataset_examples.id"],
            ondelete="SET NULL",
            name="fk_evaluation_results_dataset_example_id__dataset_examples_id",
        ),
        Index(
            "ix_evaluation_results_run_example_index",
            "optimization_run_id",
            "example_index",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    optimization_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    dataset_example_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    example_index: Mapped[int] = mapped_column(Integer, nullable=False)
    input_data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    predicted_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PromptSnapshot(Base):
    __tablename__ = "prompt_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            ondelete="CASCADE",
            name="fk_prompt_snapshots_tenant_workspace__workspaces_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["optimization_run_id"],
            ["optimization_runs.id"],
            ondelete="CASCADE",
            name="fk_prompt_snapshots_run_id__optimization_runs_id",
        ),
        Index(
            "ix_prompt_snapshots_run_created_at", "optimization_run_id", "created_at"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("app.uuid_v7()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    optimization_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    predictor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_type: Mapped[PromptSnapshotType] = mapped_column(
        _pg_enum(PromptSnapshotType, name="prompt_snapshot_type"),
        nullable=False,
    )
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
