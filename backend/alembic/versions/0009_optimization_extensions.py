"""Add LLM provider registry, dataset storage metadata, job heartbeats, and promotion bindings."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_optimization_extensions"
down_revision = "0008_optimization_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_providers",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("dialect", sa.Text(), nullable=False),
        sa.Column(
            "api_mode",
            sa.Text(),
            server_default=sa.text("'auto'"),
            nullable=False,
        ),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("api_key_ciphertext", sa.Text(), nullable=True),
        sa.Column("api_key_hint", sa.Text(), nullable=True),
        sa.Column("default_model", sa.Text(), nullable=True),
        sa.Column(
            "cached_models_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(name)) > 0",
            name="ck_llm_providers_name_nonempty",
        ),
        sa.CheckConstraint(
            "dialect IN ('openai', 'openai_compatible', 'anthropic', 'gemini', "
            "'openrouter', 'xai', 'groq', 'deepseek', 'ollama', 'vllm', 'typesafe')",
            name="ck_llm_providers_dialect",
        ),
        sa.CheckConstraint(
            "api_mode IN ('auto', 'chat', 'responses')",
            name="ck_llm_providers_api_mode",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_llm_providers_name"),
    )

    op.add_column(
        "optimization_datasets",
        sa.Column(
            "storage_backend",
            sa.Text(),
            server_default=sa.text("'neon'"),
            nullable=False,
        ),
    )
    op.add_column(
        "optimization_datasets",
        sa.Column("storage_volume", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_optimization_datasets_storage_backend",
        "optimization_datasets",
        "storage_backend IN ('neon', 'daytona_volume')",
    )

    op.add_column(
        "optimization_jobs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "optimization_jobs",
        sa.Column("decision_provider_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_optimization_jobs_decision_provider_id",
        "optimization_jobs",
        "llm_providers",
        ["decision_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_optimization_jobs_decision_provider_id",
        "optimization_jobs",
        ["decision_provider_id"],
        postgresql_where=sa.text("decision_provider_id IS NOT NULL"),
    )

    op.add_column(
        "optimization_promotions",
        sa.Column("decision_provider_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "optimization_promotions",
        sa.Column(
            "program_kind",
            sa.Text(),
            server_default=sa.text("'rlm'"),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_optimization_promotions_decision_provider_id",
        "optimization_promotions",
        "llm_providers",
        ["decision_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_optimization_promotions_decision_provider_id",
        "optimization_promotions",
        type_="foreignkey",
    )
    op.drop_column("optimization_promotions", "program_kind")
    op.drop_column("optimization_promotions", "decision_provider_id")

    op.drop_index(
        "idx_optimization_jobs_decision_provider_id",
        table_name="optimization_jobs",
    )
    op.drop_constraint(
        "fk_optimization_jobs_decision_provider_id",
        "optimization_jobs",
        type_="foreignkey",
    )
    op.drop_column("optimization_jobs", "decision_provider_id")
    op.drop_column("optimization_jobs", "heartbeat_at")

    op.drop_constraint(
        "ck_optimization_datasets_storage_backend",
        "optimization_datasets",
        type_="check",
    )
    op.drop_column("optimization_datasets", "storage_volume")
    op.drop_column("optimization_datasets", "storage_backend")

    op.drop_table("llm_providers")
