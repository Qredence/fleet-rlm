"""Add tenant-scoped performance indexes for Neon/Postgres query patterns.

Revision ID: 0007_neon_performance_indexes
Revises: 0006_modal_infra_tracking
Create Date: 2026-02-23
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0007_neon_performance_indexes"
down_revision = "0006_modal_infra_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Tenant/time/status access patterns for run dashboards and status filtering.
    op.create_index(
        "ix_runs_tenant_status_created_at",
        "runs",
        ["tenant_id", "status", "created_at"],
    )

    # Run-step timeline and execution diagnostics filtering.
    op.create_index(
        "ix_run_steps_tenant_type_created_at",
        "run_steps",
        ["tenant_id", "step_type", "created_at"],
    )
    op.create_index(
        "ix_run_steps_tenant_modal_volume_created_at",
        "run_steps",
        ["tenant_id", "modal_volume_id", "created_at"],
    )

    # Artifact lookup in per-run and per-kind canvas views.
    op.create_index(
        "ix_artifacts_tenant_run_created_at",
        "artifacts",
        ["tenant_id", "run_id", "created_at"],
    )
    op.create_index(
        "ix_artifacts_tenant_kind_created_at",
        "artifacts",
        ["tenant_id", "kind", "created_at"],
    )

    # Phase 2 RLM/DSPy tables: common metadata and correlation lookups.
    op.create_index(
        "ix_rlm_programs_tenant_updated_at",
        "rlm_programs",
        ["tenant_id", "updated_at"],
    )
    op.create_index(
        "ix_rlm_traces_tenant_program_created_at",
        "rlm_traces",
        ["tenant_id", "program_id", "created_at"],
    )
    op.create_index(
        "ix_rlm_traces_tenant_step_created_at",
        "rlm_traces",
        ["tenant_id", "run_step_id", "created_at"],
    )

    # Modal infrastructure inventory browsing by tenant/provider/environment recency.
    op.create_index(
        "ix_modal_volumes_tenant_provider_env_last_seen",
        "modal_volumes",
        ["tenant_id", "provider", "environment", "last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_modal_volumes_tenant_provider_env_last_seen", table_name="modal_volumes"
    )
    op.drop_index("ix_rlm_traces_tenant_step_created_at", table_name="rlm_traces")
    op.drop_index("ix_rlm_traces_tenant_program_created_at", table_name="rlm_traces")
    op.drop_index("ix_rlm_programs_tenant_updated_at", table_name="rlm_programs")
    op.drop_index("ix_artifacts_tenant_kind_created_at", table_name="artifacts")
    op.drop_index("ix_artifacts_tenant_run_created_at", table_name="artifacts")
    op.drop_index("ix_run_steps_tenant_modal_volume_created_at", table_name="run_steps")
    op.drop_index("ix_run_steps_tenant_type_created_at", table_name="run_steps")
    op.drop_index("ix_runs_tenant_status_created_at", table_name="runs")
