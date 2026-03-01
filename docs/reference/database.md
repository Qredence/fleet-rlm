# Database Architecture

Current backend persistence uses **Neon/Postgres** with Alembic migrations and tenant isolation via row-level security (RLS).

## Primary Data Stack

- Engine/session management: `src/fleet_rlm/db/engine.py`
- Repository layer: `src/fleet_rlm/db/repository.py`
- Domain models: `src/fleet_rlm/db/models.py`
- Migrations: `migrations/` (Alembic)

## Connection and Engine

`DatabaseManager` normalizes PostgreSQL URLs for async (`asyncpg`) and sync (Alembic `psycopg`) usage.

Key helpers:

- `to_async_database_url(...)`
- `to_sync_database_url(...)`
- `DatabaseManager.session()`

## Migration Lifecycle

Alembic environment:

- `migrations/env.py`

Baseline schema + RLS policies are defined in versioned migrations (for example `0001_neon_core_schema.py`).

## Tenant Isolation (RLS)

Repository methods set tenant context per transaction:

- `SELECT set_config('app.tenant_id', :tenant_id, true)`

RLS policies reference `current_setting('app.tenant_id', true)` to enforce tenant scoping.

## Runtime Behavior

Server startup behavior (`src/fleet_rlm/server/main.py` + config):

- if `DATABASE_URL` is configured, Neon repository is initialized
- if `DATABASE_REQUIRED=true` and URL missing, startup fails
- if database is optional and missing, runtime continues with persistence-disabled warnings

## Removed Legacy SQLite Compatibility Surface

Legacy SQLite compatibility modules and CRUD routes were removed.
The canonical persistence path is Neon/Postgres via `fleet_rlm.db.*`.
