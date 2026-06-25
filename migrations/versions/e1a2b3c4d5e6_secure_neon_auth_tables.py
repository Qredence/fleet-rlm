"""secure_neon_auth_tables

Enable Row-Level Security on all neon_auth schema tables (Better Auth) so that:

1. The neon_auth managed service role (table owner) continues to bypass RLS
   automatically (ENABLE without FORCE — owner is exempt).
2. Data API consumers using JWT authentication can only access their own auth data
   via auth.uid() / auth.user_id() from the pg_session_jwt extension.
3. System-only tables (jwks, project_config, verification) are locked down —
   no policies means no access for non-owner roles.

This protects:
- neon_auth.user (emails, hashed passwords)
- neon_auth.session (active session tokens)
- neon_auth.account (OAuth provider links)
- neon_auth.member (organization memberships)

from being read by non-owner roles (authenticated, anonymous) via the Data API
or any connection that doesn't use the neon_auth service role.

Note: neondb_owner has rolbypassrls=true (Neon platform default), so it bypasses
RLS regardless. However, the application never queries neon_auth tables directly —
auth is handled entirely through the Neon Auth HTTP API.

Revision ID: e1a2b3c4d5e6
Revises: d31f6d7a8c21
Create Date: 2026-06-25 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "e1a2b3c4d5e6"
down_revision = "d31f6d7a8c21"
branch_labels = None
depends_on = None

# Tables with a "userId" column — scope to the authenticated user.
_USER_SCOPED_TABLES = ("account", "member", "session")

# The user table — scope on "id" (the user's own row).
_USER_SELF_TABLE = "user"

# System-only tables — no policies = no access for non-owner roles.
# (jwks, project_config, verification, organization, invitation)
_SYSTEM_ONLY_TABLES = ("jwks", "project_config", "verification", "organization", "invitation")

_ALL_AUTH_TABLES = (
    _USER_SELF_TABLE,
    *_USER_SCOPED_TABLES,
    *_SYSTEM_ONLY_TABLES,
)


def upgrade() -> None:
    # ── Step 1: Enable RLS on all neon_auth tables (no FORCE) ──────────
    # The neon_auth role owns these tables and bypasses RLS automatically.
    for table in _ALL_AUTH_TABLES:
        op.execute(f"ALTER TABLE neon_auth.{table} ENABLE ROW LEVEL SECURITY")

    # ── Step 2: User-scoped SELECT policies ────────────────────────────
    # auth.uid() returns the JWT user_id as uuid (from pg_session_jwt).
    # COALESCE guards against NULL when no JWT is present (anonymous requests).

    # neon_auth.user — users can read their own row
    op.execute(
        """
        CREATE POLICY neon_auth_user_self_read ON neon_auth."user"
        FOR SELECT
        USING (id = COALESCE(auth.uid(), '00000000-0000-0000-0000-000000000000'::uuid))
        """
    )

    # neon_auth.user — users can update their own row (profile changes)
    op.execute(
        """
        CREATE POLICY neon_auth_user_self_update ON neon_auth."user"
        FOR UPDATE
        USING (id = COALESCE(auth.uid(), '00000000-0000-0000-0000-000000000000'::uuid))
        WITH CHECK (id = COALESCE(auth.uid(), '00000000-0000-0000-0000-000000000000'::uuid))
        """
    )

    # neon_auth.account — users can read their own OAuth account links
    op.execute(
        """
        CREATE POLICY neon_auth_account_self_read ON neon_auth.account
        FOR SELECT
        USING ("userId" = COALESCE(auth.uid(), '00000000-0000-0000-0000-000000000000'::uuid))
        """
    )

    # neon_auth.session — users can read their own sessions
    op.execute(
        """
        CREATE POLICY neon_auth_session_self_read ON neon_auth.session
        FOR SELECT
        USING ("userId" = COALESCE(auth.uid(), '00000000-0000-0000-0000-000000000000'::uuid))
        """
    )

    # neon_auth.session — users can update/delete their own sessions (logout)
    op.execute(
        """
        CREATE POLICY neon_auth_session_self_write ON neon_auth.session
        FOR DELETE
        USING ("userId" = COALESCE(auth.uid(), '00000000-0000-0000-0000-000000000000'::uuid))
        """
    )

    # neon_auth.member — users can read their own org memberships
    op.execute(
        """
        CREATE POLICY neon_auth_member_self_read ON neon_auth.member
        FOR SELECT
        USING ("userId" = COALESCE(auth.uid(), '00000000-0000-0000-0000-000000000000'::uuid))
        """
    )

    # ── Step 3: Organization access via membership ─────────────────────
    # Users can read orgs they belong to (via neon_auth.member join).
    op.execute(
        """
        CREATE POLICY neon_auth_org_member_read ON neon_auth.organization
        FOR SELECT
        USING (
            id IN (
                SELECT m."organizationId"
                FROM neon_auth.member m
                WHERE m."userId" = COALESCE(auth.uid(), '00000000-0000-0000-0000-000000000000'::uuid)
            )
        )
        """
    )

    # Users can read invitations for orgs they belong to, or addressed to their email.
    op.execute(
        """
        CREATE POLICY neon_auth_invitation_member_read ON neon_auth.invitation
        FOR SELECT
        USING (
            "organizationId" IN (
                SELECT m."organizationId"
                FROM neon_auth.member m
                WHERE m."userId" = COALESCE(auth.uid(), '00000000-0000-0000-0000-000000000000'::uuid)
            )
            OR email = (
                SELECT u.email
                FROM neon_auth."user" u
                WHERE u.id = COALESCE(auth.uid(), '00000000-0000-0000-0000-000000000000'::uuid)
            )
        )
        """
    )

    # ── Step 4: No policies for system-only tables ─────────────────────
    # jwks, project_config, verification have NO policies.
    # With RLS enabled and no policies, all non-owner roles see zero rows.
    # The neon_auth service role (owner) bypasses RLS and manages these tables.


def downgrade() -> None:
    # Drop all policies we created
    policy_table_pairs = [
        ("neon_auth_user_self_read", "user"),
        ("neon_auth_user_self_update", "user"),
        ("neon_auth_account_self_read", "account"),
        ("neon_auth_session_self_read", "session"),
        ("neon_auth_session_self_write", "session"),
        ("neon_auth_member_self_read", "member"),
        ("neon_auth_org_member_read", "organization"),
        ("neon_auth_invitation_member_read", "invitation"),
    ]
    for policy_name, table in policy_table_pairs:
        op.execute(f'DROP POLICY IF EXISTS {policy_name} ON neon_auth."{table}"')

    # Disable RLS on all auth tables
    for table in _ALL_AUTH_TABLES:
        op.execute(f"ALTER TABLE neon_auth.{table} DISABLE ROW LEVEL SECURITY")
