"""harden_neon_security

Revision ID: b83084c84fc2
Revises: 27834309e8c2
Create Date: 2026-06-20 07:22:01.573502
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "b83084c84fc2"
down_revision = "27834309e8c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 4. pgcrypto is installed in public. Move to app schema without dropping
    # dependent objects.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM pg_extension ext
            JOIN pg_namespace nsp ON nsp.oid = ext.extnamespace
            WHERE ext.extname = 'pgcrypto' AND nsp.nspname <> 'app'
          ) THEN
            ALTER EXTENSION pgcrypto SET SCHEMA app;
          END IF;
        END;
        $$;
        """
    )

    # 5. uuid-ossp is installed in public. Move to app schema without dropping
    # dependent objects.
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM pg_extension ext
            JOIN pg_namespace nsp ON nsp.oid = ext.extnamespace
            WHERE ext.extname = 'uuid-ossp' AND nsp.nspname <> 'app'
          ) THEN
            ALTER EXTENSION "uuid-ossp" SET SCHEMA app;
          END IF;
        END;
        $$;
        """
    )

    # Recreate app.uuid_v7 to guarantee existence and proper binding after extension moves
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.uuid_v7()
        RETURNS uuid
        LANGUAGE plpgsql
        AS $$
        DECLARE
          unix_ts_ms BIGINT;
          ts_hex TEXT;
          rand_hex TEXT;
          variant_source INTEGER;
          variant_nibble TEXT;
        BEGIN
          IF to_regprocedure('app.uuidv7()') IS NOT NULL THEN
            RETURN app.uuidv7();
          ELSIF to_regprocedure('app.uuid_generate_v7()') IS NOT NULL THEN
            RETURN app.uuid_generate_v7();
          ELSIF to_regprocedure('app.gen_random_bytes(integer)') IS NOT NULL THEN
            unix_ts_ms := floor(extract(epoch from clock_timestamp()) * 1000);
            ts_hex := lpad(to_hex(unix_ts_ms), 12, '0');
            rand_hex := substr(encode(app.gen_random_bytes(10), 'hex'), 1, 19);
            variant_source := get_byte(
              decode('0' || substr(rand_hex, 4, 1), 'hex'),
              0
            );
            variant_nibble := substr('89ab', (variant_source & 3) + 1, 1);

            RETURN (
              substr(ts_hex, 1, 8) || '-' ||
              substr(ts_hex, 9, 4) || '-' ||
              '7' || substr(rand_hex, 1, 3) || '-' ||
              variant_nibble || substr(rand_hex, 5, 3) || '-' ||
              substr(rand_hex, 8, 12)
            )::uuid;
          END IF;

          RAISE EXCEPTION
            'No UUIDv7-compatible generator found (expected uuidv7(), uuid_generate_v7(), or pgcrypto''s gen_random_bytes(integer))';
        END;
        $$;
        """
    )

    # 1. app.uuid_v7 has a role mutable search_path.
    op.execute("ALTER FUNCTION app.uuid_v7() SET search_path = pg_catalog, app")

    # 2. app.set_updated_at has a role mutable search_path.
    op.execute("ALTER FUNCTION app.set_updated_at() SET search_path = pg_catalog")

    # 3. public.show_db_tree has a role mutable search_path in managed Neon
    # projects, but it is not part of the Fleet migration chain.
    op.execute(
        """
        DO $$
        BEGIN
          IF to_regprocedure('public.show_db_tree()') IS NOT NULL THEN
            ALTER FUNCTION public.show_db_tree() SET search_path = pg_catalog, public;
          END IF;
        END;
        $$;
        """
    )

    # 6. alembic_version has RLS enabled but no policies. Disable RLS on it since it is used only for migration management.
    op.execute("ALTER TABLE public.alembic_version DISABLE ROW LEVEL SECURITY")

    # 7. llm_provider_profiles has RLS enabled but no policies. Add a policy allowing SELECT to everyone (USING (true)).
    op.execute("DROP POLICY IF EXISTS select_llm_provider_profiles_all ON public.llm_provider_profiles")
    op.execute("CREATE POLICY select_llm_provider_profiles_all ON public.llm_provider_profiles FOR SELECT USING (true)")

    # 8. llm_role_bindings has RLS enabled but no policies. Add a policy allowing SELECT to everyone (USING (true)).
    op.execute("DROP POLICY IF EXISTS select_llm_role_bindings_all ON public.llm_role_bindings")
    op.execute("CREATE POLICY select_llm_role_bindings_all ON public.llm_role_bindings FOR SELECT USING (true)")

    # 9. tenants has RLS enabled but no policies. Add a tenant-scoped policy.
    op.execute("ALTER TABLE public.tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.tenants FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_scope_tenants ON public.tenants")
    op.execute(
        """
        CREATE POLICY tenant_scope_tenants ON public.tenants
        USING (id = nullif((select current_setting('app.tenant_id', true)), '')::uuid)
        WITH CHECK (id = nullif((select current_setting('app.tenant_id', true)), '')::uuid)
        """
    )


def downgrade() -> None:
    # 9. tenants RLS policy revert
    op.execute("DROP POLICY IF EXISTS tenant_scope_tenants ON public.tenants")

    # 8. llm_role_bindings RLS policy revert
    op.execute("DROP POLICY IF EXISTS select_llm_role_bindings_all ON public.llm_role_bindings")

    # 7. llm_provider_profiles RLS policy revert
    op.execute("DROP POLICY IF EXISTS select_llm_provider_profiles_all ON public.llm_provider_profiles")

    # 6. Re-enable RLS on alembic_version (restoring original state)
    op.execute("ALTER TABLE public.alembic_version ENABLE ROW LEVEL SECURITY")

    # 5. Move uuid-ossp back to public schema
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM pg_extension ext
            JOIN pg_namespace nsp ON nsp.oid = ext.extnamespace
            WHERE ext.extname = 'uuid-ossp' AND nsp.nspname <> 'public'
          ) THEN
            ALTER EXTENSION "uuid-ossp" SET SCHEMA public;
          END IF;
        END;
        $$;
        """
    )

    # 4. Move pgcrypto back to public schema
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM pg_extension ext
            JOIN pg_namespace nsp ON nsp.oid = ext.extnamespace
            WHERE ext.extname = 'pgcrypto' AND nsp.nspname <> 'public'
          ) THEN
            ALTER EXTENSION pgcrypto SET SCHEMA public;
          END IF;
        END;
        $$;
        """
    )

    # 3. Reset public.show_db_tree search_path
    op.execute(
        """
        DO $$
        BEGIN
          IF to_regprocedure('public.show_db_tree()') IS NOT NULL THEN
            ALTER FUNCTION public.show_db_tree() RESET search_path;
          END IF;
        END;
        $$;
        """
    )

    # Recreate app.uuid_v7 with pre-hardening unqualified extension calls so it
    # keeps working after extension rollback to public.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.uuid_v7()
        RETURNS uuid
        LANGUAGE plpgsql
        AS $$
        DECLARE
          unix_ts_ms BIGINT;
          ts_hex TEXT;
          rand_hex TEXT;
          variant_source INTEGER;
          variant_nibble TEXT;
        BEGIN
          IF to_regprocedure('uuidv7()') IS NOT NULL THEN
            RETURN uuidv7();
          ELSIF to_regprocedure('uuid_generate_v7()') IS NOT NULL THEN
            RETURN uuid_generate_v7();
          ELSIF to_regprocedure('gen_random_bytes(integer)') IS NOT NULL THEN
            unix_ts_ms := floor(extract(epoch from clock_timestamp()) * 1000);
            ts_hex := lpad(to_hex(unix_ts_ms), 12, '0');
            rand_hex := substr(encode(gen_random_bytes(10), 'hex'), 1, 19);
            variant_source := get_byte(
              decode('0' || substr(rand_hex, 4, 1), 'hex'),
              0
            );
            variant_nibble := substr('89ab', (variant_source & 3) + 1, 1);

            RETURN (
              substr(ts_hex, 1, 8) || '-' ||
              substr(ts_hex, 9, 4) || '-' ||
              '7' || substr(rand_hex, 1, 3) || '-' ||
              variant_nibble || substr(rand_hex, 5, 3) || '-' ||
              substr(rand_hex, 8, 12)
            )::uuid;
          END IF;

          RAISE EXCEPTION
            'No UUIDv7-compatible generator found (expected uuidv7(), uuid_generate_v7(), or pgcrypto''s gen_random_bytes(integer))';
        END;
        $$;
        """
    )

    # 2. Reset app.set_updated_at search_path
    op.execute("ALTER FUNCTION app.set_updated_at() RESET search_path")

    # 1. Reset app.uuid_v7 search_path
    op.execute("ALTER FUNCTION app.uuid_v7() RESET search_path")
