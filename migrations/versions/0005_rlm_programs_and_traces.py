"""Add core RLM/DSPy program and trace tables.

Revision ID: 0005_rlm_programs_and_traces
Revises: 0004_remove_deprecated_skills_taxonomy
Create Date: 2026-02-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0005_rlm_programs_and_traces"
down_revision = "0004_remove_deprecated_skills_taxonomy"
branch_labels = None
depends_on = None

RLS_TABLES = ["rlm_programs", "rlm_traces"]


def _enable_rls(table_name: str) -> None:
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation_{table_name}
        ON {table_name}
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
        """
    )


def upgrade() -> None:
    op.create_table(
        "rlm_programs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("program_key", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column(
            "kind",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'compiled'"),
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("dspy_signature", sa.String(length=255), nullable=True),
        sa.Column("version_tag", sa.String(length=128), nullable=True),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "program_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_rlm_programs_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "program_key", name="uq_rlm_programs_tenant_key"
        ),
    )
    op.create_index(
        "ix_rlm_programs_tenant_created_at",
        "rlm_programs",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_rlm_programs_tenant_kind_status",
        "rlm_programs",
        ["tenant_id", "kind", "status"],
    )

    op.create_table(
        "rlm_traces",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_step_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "trace_kind",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'trajectory'"),
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'captured'"),
        ),
        sa.Column(
            "source",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'rlm'"),
        ),
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "program_id"],
            ["rlm_programs.tenant_id", "rlm_programs.id"],
            ondelete="SET NULL",
            name="fk_rlm_traces_tenant_program__rlm_programs_tenant_id_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["runs.tenant_id", "runs.id"],
            ondelete="CASCADE",
            name="fk_rlm_traces_tenant_run__runs_tenant_id_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_step_id"],
            ["run_steps.tenant_id", "run_steps.id"],
            ondelete="SET NULL",
            name="fk_rlm_traces_tenant_step__run_steps_tenant_id_id",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_rlm_traces_tenant_id_id"),
    )
    op.create_index(
        "ix_rlm_traces_tenant_created_at",
        "rlm_traces",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_rlm_traces_tenant_run_created_at",
        "rlm_traces",
        ["tenant_id", "run_id", "created_at"],
    )
    op.create_index(
        "ix_rlm_traces_tenant_kind_status",
        "rlm_traces",
        ["tenant_id", "trace_kind", "status"],
    )

    for table_name in RLS_TABLES:
        _enable_rls(table_name)


def downgrade() -> None:
    for table_name in reversed(RLS_TABLES):
        op.execute(
            f"DROP POLICY IF EXISTS tenant_isolation_{table_name} ON {table_name}"
        )

    op.drop_index("ix_rlm_traces_tenant_kind_status", table_name="rlm_traces")
    op.drop_index("ix_rlm_traces_tenant_run_created_at", table_name="rlm_traces")
    op.drop_index("ix_rlm_traces_tenant_created_at", table_name="rlm_traces")
    op.drop_table("rlm_traces")

    op.drop_index("ix_rlm_programs_tenant_kind_status", table_name="rlm_programs")
    op.drop_index("ix_rlm_programs_tenant_created_at", table_name="rlm_programs")
    op.drop_table("rlm_programs")
