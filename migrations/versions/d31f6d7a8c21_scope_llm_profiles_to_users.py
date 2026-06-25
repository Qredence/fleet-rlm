"""scope_llm_profiles_to_users

Scope llm_provider_profiles and llm_role_bindings to (tenant_id, user_id) and
enable + force Row-Level Security on both tables so each user only sees their own
BYOK provider profiles and role bindings.

Upgrade note: rows created before this migration have NULL tenant_id/user_id. Under
forced RLS they never match the policy and become invisible to every role (including
the table owner) — they are hidden, not deleted. Hosted first-deploys have no prior
rows. To re-claim pre-existing rows, run as a BYPASSRLS role (e.g. superuser):

    UPDATE llm_provider_profiles
       SET tenant_id = '<tenant-uuid>', user_id = '<user-uuid>'
     WHERE tenant_id IS NULL AND user_id IS NULL;
    UPDATE llm_role_bindings
       SET tenant_id = '<tenant-uuid>', user_id = '<user-uuid>'
     WHERE tenant_id IS NULL AND user_id IS NULL;

The RLS policies read app.tenant_id / app.user_id session GUCs set per transaction by
PostgresLlmProfileStore._set_request_context (set_config(..., true)), deliberately not
Neon's gateway-set auth.user_id(), so the same policies work across dev/entra/neon modes.

Revision ID: d31f6d7a8c21
Revises: b83084c84fc2
Create Date: 2026-06-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d31f6d7a8c21"
down_revision = "b83084c84fc2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_provider_profiles", sa.Column("tenant_id", sa.UUID(), nullable=True))
    op.add_column("llm_provider_profiles", sa.Column("user_id", sa.UUID(), nullable=True))
    op.add_column("llm_provider_profiles", sa.Column("workspace_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_llm_provider_profiles_tenant_user",
        "llm_provider_profiles",
        "users",
        ["tenant_id", "user_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_llm_provider_profiles_tenant_workspace",
        "llm_provider_profiles",
        "workspaces",
        ["tenant_id", "workspace_id"],
        ["tenant_id", "id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_llm_provider_profiles_tenant_user_id",
        "llm_provider_profiles",
        ["tenant_id", "user_id", "id"],
    )
    op.create_index(
        "ix_llm_provider_profiles_tenant_user_name",
        "llm_provider_profiles",
        ["tenant_id", "user_id", "name"],
    )

    op.add_column(
        "llm_role_bindings", sa.Column("id", sa.UUID(), server_default=sa.text("app.uuid_v7()"), nullable=True)
    )
    op.add_column("llm_role_bindings", sa.Column("tenant_id", sa.UUID(), nullable=True))
    op.add_column("llm_role_bindings", sa.Column("user_id", sa.UUID(), nullable=True))
    op.add_column("llm_role_bindings", sa.Column("workspace_id", sa.UUID(), nullable=True))
    op.execute("UPDATE public.llm_role_bindings SET id = app.uuid_v7() WHERE id IS NULL")
    op.alter_column("llm_role_bindings", "id", nullable=False)
    op.drop_constraint("llm_role_bindings_pkey", "llm_role_bindings", type_="primary")
    op.create_primary_key("llm_role_bindings_pkey", "llm_role_bindings", ["id"])
    op.create_foreign_key(
        "fk_llm_role_bindings_tenant_user",
        "llm_role_bindings",
        "users",
        ["tenant_id", "user_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_llm_role_bindings_tenant_workspace",
        "llm_role_bindings",
        "workspaces",
        ["tenant_id", "workspace_id"],
        ["tenant_id", "id"],
        ondelete="SET NULL",
    )
    op.drop_constraint("llm_role_bindings_profile_id_fkey", "llm_role_bindings", type_="foreignkey")
    op.create_foreign_key(
        "fk_llm_role_bindings_scoped_profile",
        "llm_role_bindings",
        "llm_provider_profiles",
        ["tenant_id", "user_id", "profile_id"],
        ["tenant_id", "user_id", "id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_llm_role_bindings_tenant_user_role",
        "llm_role_bindings",
        ["tenant_id", "user_id", "role"],
    )
    op.create_index("ix_llm_role_bindings_tenant_user", "llm_role_bindings", ["tenant_id", "user_id"])

    for table_name in ("llm_provider_profiles", "llm_role_bindings"):
        op.execute(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS select_llm_provider_profiles_all ON public.llm_provider_profiles")
    op.execute("DROP POLICY IF EXISTS select_llm_role_bindings_all ON public.llm_role_bindings")
    op.execute("DROP POLICY IF EXISTS user_scope_llm_provider_profiles ON public.llm_provider_profiles")
    op.execute("DROP POLICY IF EXISTS user_scope_llm_role_bindings ON public.llm_role_bindings")
    op.execute(
        """
        CREATE POLICY user_scope_llm_provider_profiles ON public.llm_provider_profiles
        USING (
          tenant_id = nullif((select current_setting('app.tenant_id', true)), '')::uuid
          AND user_id = nullif((select current_setting('app.user_id', true)), '')::uuid
        )
        WITH CHECK (
          tenant_id = nullif((select current_setting('app.tenant_id', true)), '')::uuid
          AND user_id = nullif((select current_setting('app.user_id', true)), '')::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY user_scope_llm_role_bindings ON public.llm_role_bindings
        USING (
          tenant_id = nullif((select current_setting('app.tenant_id', true)), '')::uuid
          AND user_id = nullif((select current_setting('app.user_id', true)), '')::uuid
        )
        WITH CHECK (
          tenant_id = nullif((select current_setting('app.tenant_id', true)), '')::uuid
          AND user_id = nullif((select current_setting('app.user_id', true)), '')::uuid
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS user_scope_llm_role_bindings ON public.llm_role_bindings")
    op.execute("DROP POLICY IF EXISTS user_scope_llm_provider_profiles ON public.llm_provider_profiles")
    op.execute("CREATE POLICY select_llm_provider_profiles_all ON public.llm_provider_profiles FOR SELECT USING (true)")
    op.execute("CREATE POLICY select_llm_role_bindings_all ON public.llm_role_bindings FOR SELECT USING (true)")

    op.drop_index("ix_llm_role_bindings_tenant_user", table_name="llm_role_bindings")
    op.drop_constraint("uq_llm_role_bindings_tenant_user_role", "llm_role_bindings", type_="unique")
    op.drop_constraint("fk_llm_role_bindings_scoped_profile", "llm_role_bindings", type_="foreignkey")
    op.create_foreign_key(
        "llm_role_bindings_profile_id_fkey",
        "llm_role_bindings",
        "llm_provider_profiles",
        ["profile_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint("fk_llm_role_bindings_tenant_workspace", "llm_role_bindings", type_="foreignkey")
    op.drop_constraint("fk_llm_role_bindings_tenant_user", "llm_role_bindings", type_="foreignkey")
    op.drop_constraint("llm_role_bindings_pkey", "llm_role_bindings", type_="primary")
    op.create_primary_key("llm_role_bindings_pkey", "llm_role_bindings", ["role"])
    op.drop_column("llm_role_bindings", "workspace_id")
    op.drop_column("llm_role_bindings", "user_id")
    op.drop_column("llm_role_bindings", "tenant_id")
    op.drop_column("llm_role_bindings", "id")

    op.drop_index("ix_llm_provider_profiles_tenant_user_name", table_name="llm_provider_profiles")
    op.drop_constraint("uq_llm_provider_profiles_tenant_user_id", "llm_provider_profiles", type_="unique")
    op.drop_constraint("fk_llm_provider_profiles_tenant_workspace", "llm_provider_profiles", type_="foreignkey")
    op.drop_constraint("fk_llm_provider_profiles_tenant_user", "llm_provider_profiles", type_="foreignkey")
    op.drop_column("llm_provider_profiles", "workspace_id")
    op.drop_column("llm_provider_profiles", "user_id")
    op.drop_column("llm_provider_profiles", "tenant_id")
