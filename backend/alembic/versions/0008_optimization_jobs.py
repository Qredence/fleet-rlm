"""Track optimization datasets, offline optimization jobs, and live artifact promotions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_optimization_jobs"
down_revision = "0007_turn_lessons"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "optimization_datasets",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column(
            "summary_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "target IN ('rlm', 'skills', 'memory')",
            name="ck_optimization_datasets_target",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_optimization_datasets_name"),
    )

    op.create_table(
        "optimization_jobs",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
        ),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("program_kind", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column(
            "stage",
            sa.Text(),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("dataset_source", sa.Text(), nullable=False),
        sa.Column("dataset_name", sa.Text(), nullable=True),
        sa.Column("dataset_id", sa.Uuid(), nullable=True),
        sa.Column(
            "config_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "progress_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "report_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "log_tail",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("saved", sa.Boolean(), nullable=True),
        sa.Column("baseline_score", sa.Float(), nullable=True),
        sa.Column("optimized_score", sa.Float(), nullable=True),
        sa.Column("train_size", sa.Integer(), nullable=True),
        sa.Column("val_size", sa.Integer(), nullable=True),
        sa.Column("holdout_size", sa.Integer(), nullable=True),
        sa.Column("artifact_object_key", sa.Text(), nullable=True),
        sa.Column("artifact_sha256", sa.CHAR(length=64), nullable=True),
        sa.Column("artifact_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("resolved_decision_model", sa.Text(), nullable=True),
        sa.Column("mlflow_run_id", sa.Text(), nullable=True),
        sa.Column("mlflow_experiment", sa.Text(), nullable=True),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "target IN ('rlm', 'skills', 'memory')",
            name="ck_optimization_jobs_target",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_optimization_jobs_status",
        ),
        sa.CheckConstraint(
            "dataset_source IN ('builtin', 'custom', 'db')",
            name="ck_optimization_jobs_dataset_source",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["optimization_datasets.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_optimization_jobs_one_active_per_target",
        "optimization_jobs",
        ["target"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "idx_optimization_jobs_created",
        "optimization_jobs",
        [sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "idx_optimization_jobs_target_created",
        "optimization_jobs",
        ["target", sa.text("created_at DESC"), sa.text("id DESC")],
    )

    op.create_table(
        "optimization_promotions",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
        ),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_object_key", sa.Text(), nullable=False),
        sa.Column("artifact_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("artifact_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("resolved_decision_model", sa.Text(), nullable=True),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "promoted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "target IN ('rlm', 'skills', 'memory')",
            name="ck_optimization_promotions_target",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["optimization_jobs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_optimization_promotions_one_active_per_target",
        "optimization_promotions",
        ["target"],
        unique=True,
        postgresql_where=sa.text("active = true"),
    )
    op.create_index(
        "idx_optimization_promotions_target_promoted",
        "optimization_promotions",
        ["target", sa.text("promoted_at DESC"), sa.text("id DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_optimization_promotions_target_promoted", table_name="optimization_promotions"
    )
    op.drop_index(
        "uq_optimization_promotions_one_active_per_target", table_name="optimization_promotions"
    )
    op.drop_table("optimization_promotions")

    op.drop_index("idx_optimization_jobs_target_created", table_name="optimization_jobs")
    op.drop_index("idx_optimization_jobs_created", table_name="optimization_jobs")
    op.drop_index("uq_optimization_jobs_one_active_per_target", table_name="optimization_jobs")
    op.drop_table("optimization_jobs")

    op.drop_table("optimization_datasets")
