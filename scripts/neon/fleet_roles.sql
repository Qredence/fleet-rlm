-- Run as the database owner inside the guarded rehearsal transaction.
-- These CREATEs intentionally reject pre-existing roles with unknown privileges.
CREATE ROLE app_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE app_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT app_migrator TO CURRENT_USER;

DO $$
DECLARE
    relation regclass;
    db text := current_database();
BEGIN
    EXECUTE format('REVOKE CREATE, TEMP ON DATABASE %I FROM PUBLIC', db);
    EXECUTE format('GRANT CONNECT, TEMP, CREATE ON DATABASE %I TO app_migrator', db);
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO app_runtime', db);
    ALTER SCHEMA public OWNER TO app_migrator;
    EXECUTE format('REVOKE CREATE ON DATABASE %I FROM app_migrator', db);
    REVOKE CREATE ON SCHEMA public FROM PUBLIC;
    GRANT USAGE ON SCHEMA public TO app_runtime;

    -- Transfer Fleet relations only. Extension-owned objects retain their owner.
    FOR relation IN
        SELECT c.oid::regclass FROM pg_class c
        WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r'
          AND c.relname IN ('sessions', 'runs', 'files', 'memories', 'workspace_settings',
              'workspace_object_cleanup', 'llm_providers', 'optimization_datasets',
              'optimization_jobs', 'optimization_promotions', 'alembic_version')
    LOOP
        EXECUTE format('ALTER TABLE %s OWNER TO app_migrator', relation);
        IF relation <> 'public.alembic_version'::regclass THEN
            EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO app_runtime', relation);
        END IF;
    END LOOP;
END $$;

ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO app_runtime;
