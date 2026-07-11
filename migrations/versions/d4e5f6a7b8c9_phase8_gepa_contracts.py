"""phase8 GEPA dataset, lifecycle, and activation contracts

Revision ID: d4e5f6a7b8c9
Revises: c3d9f1a7e2b4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d4e5f6a7b8c9"
down_revision = "c3d9f1a7e2b4"
branch_labels = None
depends_on = None


def _tenant_policy(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def upgrade() -> None:
    op.execute("ALTER TYPE optimization_run_status ADD VALUE IF NOT EXISTS 'queued'")
    op.execute("ALTER TYPE optimization_run_status ADD VALUE IF NOT EXISTS 'interrupted'")

    op.add_column("datasets", sa.Column("version", sa.Integer(), server_default="1", nullable=False))
    op.add_column("datasets", sa.Column("supersedes_dataset_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("datasets", sa.Column("content_sha256", sa.String(64), nullable=True))
    op.add_column("datasets", sa.Column("eligibility", sa.String(32), server_default="draft", nullable=False))
    op.add_column("datasets", sa.Column("consent_status", sa.String(32), server_default="unreviewed", nullable=False))
    op.add_column("datasets", sa.Column("redaction_status", sa.String(32), server_default="unreviewed", nullable=False))
    op.add_column("datasets", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("datasets", sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_datasets_supersedes_dataset_id",
        "datasets",
        "datasets",
        ["supersedes_dataset_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_datasets_supersedes_dataset_id", "datasets", ["supersedes_dataset_id"])
    op.create_check_constraint("ck_datasets_version_positive", "datasets", "version >= 1")
    op.create_check_constraint("ck_datasets_eligibility", "datasets", "eligibility IN ('draft', 'approved')")
    op.create_check_constraint(
        "ck_datasets_consent_status", "datasets", "consent_status IN ('unreviewed', 'approved', 'rejected')"
    )
    op.create_check_constraint(
        "ck_datasets_redaction_status", "datasets", "redaction_status IN ('unreviewed', 'approved', 'rejected')"
    )
    op.create_check_constraint(
        "ck_datasets_approved_has_timestamp", "datasets", "eligibility <> 'approved' OR approved_at IS NOT NULL"
    )
    op.create_foreign_key(
        "fk_datasets_approved_by_user_id", "datasets", "users", ["approved_by_user_id"], ["id"], ondelete="SET NULL"
    )

    op.add_column(
        "dataset_examples", sa.Column("partition", sa.String(32), server_default="unassigned", nullable=False)
    )
    op.add_column("dataset_examples", sa.Column("content_sha256", sa.String(64), nullable=True))
    op.create_check_constraint(
        "ck_dataset_examples_partition",
        "dataset_examples",
        "partition IN ('training', 'selection', 'promotion_test', 'unassigned')",
    )

    op.add_column("optimization_runs", sa.Column("run_fingerprint", sa.String(64), nullable=True))
    op.add_column("optimization_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("optimization_runs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("optimization_runs", sa.Column("attempt", sa.Integer(), server_default="1", nullable=False))

    op.create_table(
        "optimization_artifact_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("app.uuid_v7()"), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("optimization_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=False),
        sa.Column("artifact_kind", sa.String(32), nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), server_default="candidate", nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["optimization_run_id"], ["optimization_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("optimization_run_id", name="uq_opt_artifact_versions_run"),
    )
    op.create_index(
        "ix_opt_artifact_versions_target",
        "optimization_artifact_versions",
        ["workspace_id", "target_kind", "target_id", "created_at"],
    )

    op.create_table(
        "optimization_target_activations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("app.uuid_v7()"), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=False),
        sa.Column("active_artifact_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_artifact_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"], ["workspaces.tenant_id", "workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["active_artifact_version_id"], ["optimization_artifact_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["previous_artifact_version_id"], ["optimization_artifact_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("workspace_id", "target_kind", "target_id", name="uq_opt_target_activation_workspace"),
    )
    _tenant_policy("optimization_artifact_versions")
    _tenant_policy("optimization_target_activations")


def downgrade() -> None:
    op.drop_table("optimization_target_activations")
    op.drop_index("ix_opt_artifact_versions_target", table_name="optimization_artifact_versions")
    op.drop_table("optimization_artifact_versions")
    op.drop_column("optimization_runs", "attempt")
    op.drop_column("optimization_runs", "cancel_requested_at")
    op.drop_column("optimization_runs", "heartbeat_at")
    op.drop_column("optimization_runs", "run_fingerprint")
    op.drop_column("dataset_examples", "content_sha256")
    op.drop_constraint("ck_dataset_examples_partition", "dataset_examples", type_="check")
    op.drop_column("dataset_examples", "partition")
    op.drop_constraint("ck_datasets_approved_has_timestamp", "datasets", type_="check")
    op.drop_constraint("ck_datasets_redaction_status", "datasets", type_="check")
    op.drop_constraint("ck_datasets_consent_status", "datasets", type_="check")
    op.drop_constraint("ck_datasets_eligibility", "datasets", type_="check")
    op.drop_constraint("ck_datasets_version_positive", "datasets", type_="check")
    op.drop_index("ix_datasets_supersedes_dataset_id", table_name="datasets")
    op.drop_constraint("fk_datasets_approved_by_user_id", "datasets", type_="foreignkey")
    op.drop_constraint("fk_datasets_supersedes_dataset_id", "datasets", type_="foreignkey")
    for column in (
        "approved_by_user_id",
        "approved_at",
        "redaction_status",
        "consent_status",
        "eligibility",
        "content_sha256",
        "supersedes_dataset_id",
        "version",
    ):
        op.drop_column("datasets", column)
    # PostgreSQL enum values are intentionally retained on downgrade.
