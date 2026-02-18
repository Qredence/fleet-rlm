"""Initial Neon multi-tenant schema for fleet-rlm.

Revision ID: 0001_neon_core_schema
Revises:
Create Date: 2026-02-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001_neon_core_schema"
down_revision = None
branch_labels = None
depends_on = None


tenant_plan = postgresql.ENUM(
    "free", "team", "enterprise", name="tenant_plan", create_type=False
)
tenant_status = postgresql.ENUM(
    "active", "suspended", "deleted", name="tenant_status", create_type=False
)
membership_role = postgresql.ENUM(
    "owner", "admin", "member", "viewer", name="membership_role", create_type=False
)
sandbox_provider = postgresql.ENUM(
    "modal", "daytona", "aca_jobs", "local", name="sandbox_provider", create_type=False
)
sandbox_session_status = postgresql.ENUM(
    "active", "ended", "failed", name="sandbox_session_status", create_type=False
)
run_status = postgresql.ENUM(
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    name="run_status",
    create_type=False,
)
run_step_type = postgresql.ENUM(
    "tool_call",
    "repl_exec",
    "llm_call",
    "retrieval",
    "guardrail",
    "summary",
    "memory",
    "output",
    "status",
    name="run_step_type",
    create_type=False,
)
job_type = postgresql.ENUM(
    "run_task",
    "memory_compaction",
    "evaluation",
    "maintenance",
    name="job_type",
    create_type=False,
)
job_status = postgresql.ENUM(
    "queued",
    "leased",
    "running",
    "succeeded",
    "failed",
    "dead",
    name="job_status",
    create_type=False,
)
memory_scope = postgresql.ENUM(
    "user", "tenant", "run", "agent", name="memory_scope", create_type=False
)
memory_kind = postgresql.ENUM(
    "note",
    "summary",
    "fact",
    "preference",
    "context",
    name="memory_kind",
    create_type=False,
)
memory_source = postgresql.ENUM(
    "user_input",
    "system",
    "tool",
    "llm",
    "imported",
    name="memory_source",
    create_type=False,
)
artifact_kind = postgresql.ENUM(
    "file",
    "log",
    "report",
    "trace",
    "image",
    "data",
    name="artifact_kind",
    create_type=False,
)
billing_source = postgresql.ENUM(
    "azure_marketplace", "manual", name="billing_source", create_type=False
)
subscription_status = postgresql.ENUM(
    "trial",
    "active",
    "past_due",
    "cancelled",
    "expired",
    name="subscription_status",
    create_type=False,
)

ENUMS = [
    tenant_plan,
    tenant_status,
    membership_role,
    sandbox_provider,
    sandbox_session_status,
    run_status,
    run_step_type,
    job_type,
    job_status,
    memory_scope,
    memory_kind,
    memory_source,
    artifact_kind,
    billing_source,
    subscription_status,
]

RLS_TABLES = [
    "users",
    "memberships",
    "sandbox_sessions",
    "runs",
    "run_steps",
    "artifacts",
    "memory_items",
    "jobs",
    "tenant_subscriptions",
]


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
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    bind = op.get_bind()
    for enum_type in ENUMS:
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "tenants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("entra_tenant_id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column(
            "plan", tenant_plan, nullable=False, server_default=sa.text("'free'")
        ),
        sa.Column(
            "status",
            tenant_status,
            nullable=False,
            server_default=sa.text("'active'"),
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
        sa.UniqueConstraint("entra_tenant_id", name="uq_tenants_entra_tenant_id"),
    )

    op.create_table(
        "users",
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
        sa.Column("entra_user_id", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
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
        sa.UniqueConstraint(
            "tenant_id", "entra_user_id", name="uq_users_tenant_entra_user"
        ),
    )

    op.create_table(
        "memberships",
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
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            membership_role,
            nullable=False,
            server_default=sa.text("'member'"),
        ),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
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
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),
    )

    op.create_table(
        "sandbox_sessions",
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
        sa.Column("provider", sandbox_provider, nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sandbox_session_status,
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "external_id",
            name="uq_sandbox_sessions_tenant_provider_external",
        ),
    )

    op.create_table(
        "runs",
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
        sa.Column("external_run_id", sa.String(length=255), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "status", run_status, nullable=False, server_default=sa.text("'running'")
        ),
        sa.Column("model_provider", sa.String(length=128), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("sandbox_provider", sandbox_provider, nullable=True),
        sa.Column(
            "sandbox_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sandbox_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("error_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "tenant_id", "external_run_id", name="uq_runs_tenant_external_run"
        ),
    )

    op.create_table(
        "run_steps",
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
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_type", run_step_type, nullable=False),
        sa.Column("input_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "output_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True
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
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "step_index",
            name="uq_run_steps_tenant_run_step_index",
        ),
    )

    op.create_table(
        "artifacts",
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
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "step_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("run_steps.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", artifact_kind, nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("checksum", sa.String(length=255), nullable=True),
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
    )

    op.create_table(
        "memory_items",
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
        sa.Column("scope", memory_scope, nullable=False),
        sa.Column("scope_id", sa.String(length=255), nullable=False),
        sa.Column("kind", memory_kind, nullable=False),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column(
            "content_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("source", memory_source, nullable=False),
        sa.Column(
            "importance",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
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
        sa.CheckConstraint(
            "importance >= 0 AND importance <= 100",
            name="ck_memory_items_importance_range",
        ),
    )

    op.create_table(
        "jobs",
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
        sa.Column("job_type", job_type, nullable=False),
        sa.Column(
            "status",
            job_status,
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "max_attempts", sa.Integer(), nullable=False, server_default=sa.text("5")
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("last_error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_jobs_tenant_idempotency_key",
        ),
    )

    op.create_table(
        "tenant_subscriptions",
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
        sa.Column(
            "billing_source",
            billing_source,
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
        sa.Column("subscription_id", sa.String(length=255), nullable=False),
        sa.Column("offer_id", sa.String(length=255), nullable=True),
        sa.Column("plan_id", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            subscription_status,
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "tenant_id",
            "billing_source",
            "subscription_id",
            name="uq_tenant_subscriptions_source_subscription",
        ),
    )

    op.create_index("ix_users_tenant_created_at", "users", ["tenant_id", "created_at"])
    op.create_index(
        "ix_memberships_tenant_created_at", "memberships", ["tenant_id", "created_at"]
    )
    op.create_index(
        "ix_sandbox_sessions_tenant_created_at",
        "sandbox_sessions",
        ["tenant_id", "created_at"],
    )
    op.create_index("ix_runs_tenant_created_at", "runs", ["tenant_id", "created_at"])
    op.create_index(
        "ix_run_steps_tenant_created_at", "run_steps", ["tenant_id", "created_at"]
    )
    op.create_index("ix_run_steps_run_id", "run_steps", ["run_id"])
    op.create_index(
        "ix_run_steps_tenant_run_step",
        "run_steps",
        ["tenant_id", "run_id", "step_index"],
    )
    op.create_index(
        "ix_artifacts_tenant_created_at", "artifacts", ["tenant_id", "created_at"]
    )
    op.create_index(
        "ix_memory_items_tenant_created_at", "memory_items", ["tenant_id", "created_at"]
    )
    op.create_index(
        "ix_memory_items_scope",
        "memory_items",
        ["tenant_id", "scope", "scope_id", "created_at"],
    )
    op.create_index(
        "ix_memory_items_tags",
        "memory_items",
        ["tags"],
        postgresql_using="gin",
    )
    op.create_index("ix_jobs_status_available_at", "jobs", ["status", "available_at"])
    op.create_index(
        "ix_jobs_tenant_status_available",
        "jobs",
        ["tenant_id", "status", "available_at"],
    )
    op.create_index("ix_jobs_tenant_created_at", "jobs", ["tenant_id", "created_at"])
    op.create_index(
        "ix_tenant_subscriptions_tenant_created_at",
        "tenant_subscriptions",
        ["tenant_id", "created_at"],
    )

    for table_name in RLS_TABLES:
        _enable_rls(table_name)


def downgrade() -> None:
    for table_name in reversed(RLS_TABLES):
        op.execute(
            f"DROP POLICY IF EXISTS tenant_isolation_{table_name} ON {table_name}"
        )

    op.drop_index(
        "ix_tenant_subscriptions_tenant_created_at", table_name="tenant_subscriptions"
    )
    op.drop_index("ix_jobs_tenant_created_at", table_name="jobs")
    op.drop_index("ix_jobs_tenant_status_available", table_name="jobs")
    op.drop_index("ix_jobs_status_available_at", table_name="jobs")
    op.drop_index("ix_memory_items_tags", table_name="memory_items")
    op.drop_index("ix_memory_items_scope", table_name="memory_items")
    op.drop_index("ix_memory_items_tenant_created_at", table_name="memory_items")
    op.drop_index("ix_artifacts_tenant_created_at", table_name="artifacts")
    op.drop_index("ix_run_steps_tenant_run_step", table_name="run_steps")
    op.drop_index("ix_run_steps_run_id", table_name="run_steps")
    op.drop_index("ix_run_steps_tenant_created_at", table_name="run_steps")
    op.drop_index("ix_runs_tenant_created_at", table_name="runs")
    op.drop_index(
        "ix_sandbox_sessions_tenant_created_at", table_name="sandbox_sessions"
    )
    op.drop_index("ix_memberships_tenant_created_at", table_name="memberships")
    op.drop_index("ix_users_tenant_created_at", table_name="users")

    op.drop_table("tenant_subscriptions")
    op.drop_table("jobs")
    op.drop_table("memory_items")
    op.drop_table("artifacts")
    op.drop_table("run_steps")
    op.drop_table("runs")
    op.drop_table("sandbox_sessions")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("tenants")

    bind = op.get_bind()
    for enum_type in reversed(ENUMS):
        enum_type.drop(bind, checkfirst=True)
