# Lakebase Postgres

How to provision a Databricks Lakebase (Postgres Autoscaling) database for Fleet
RLM and point the TOML-declared `FLEET_DATABASE_URL` at it. The committed
`daytona-recursive` profile keeps its supervised local MLflow server; to route
traces to managed Databricks MLflow instead, declare a local profile with
`mlflow.tracking_uri = "databricks"` and the `mlflow.*_env` references in
`config/fleet.toml`.

Lakebase is Databricks' serverless Postgres. A **project** is the top-level
container; it auto-provisions a `production` branch and a `primary` read-write
endpoint. Roles and databases live under a branch.

```text
Project (fleet-rlm)
  └── Branch (production)
        ├── Endpoint (primary, read-write)
        ├── Database (fleet_rlm)
        └── Role (fleet_app)
```

Fleet RLM accepts a PostgreSQL URL with `sslmode=require` and normalizes it to
asyncpg's `ssl` connection option. Alembic uses the synchronous psycopg driver
and owns Fleet's schema (`migrations/`); `scripts/db_init.py` applies the chain
to head. Obtain actual endpoint and database names from the Lakebase Connect
dialog rather than assuming the example names below.

## Prerequisites

- Databricks CLI >= v0.294.0, authenticated: `databricks auth profiles`
- A profile with permission to manage the project (`--profile <PROFILE>` below)

## 1. Create (or locate) the project

```bash
databricks postgres create-project fleet-rlm \
  --json '{"spec": {"display_name": "Fleet RLM"}}' \
  --profile <PROFILE>
```

Resolve the endpoint host:

```bash
databricks postgres get-endpoint \
  projects/fleet-rlm/branches/production/endpoints/primary \
  --profile <PROFILE> -o json
# host is at: .status.hosts.host
```

## 2. Enable native password login

Native login lets a durable Postgres role authenticate with a password instead
of a short-lived OAuth token.

New projects disable password connections by default. Enable them explicitly
when choosing this authentication method; see the official
[role and password-connection guidance](https://docs.databricks.com/aws/en/oltp/projects/manage-roles).

```bash
databricks postgres update-project projects/fleet-rlm spec.enable_pg_native_login \
  --json '{"spec": {"enable_pg_native_login": true}}' --profile <PROFILE>
```

## 3. Create the `fleet_app` role (recommended)

Connect with an owner identity (step 4 shows how to get a token for psql) and
create the durable role. `fleet_app` should own all `fleet_*` tables and
`alembic_version`, so it can run both runtime DML and migrations.

```sql
CREATE ROLE fleet_app WITH LOGIN PASSWORD '<strong-password>';
CREATE DATABASE fleet_rlm OWNER fleet_app;
```

Grant ownership/membership as your deployment requires. **Never commit the
password.**

## 4. Alternative: short-lived OAuth token

For quick sessions you can use an OAuth role for a Databricks identity. Tokens
expire after one hour and require rotation for a long-running application.
Fleet's static database URL does not implement a credential-refresh callback;
pool pre-ping and recycling do not refresh tokens. See the official
[connection guide](https://docs.databricks.com/aws/en/oltp/projects/connect-overview).

```bash
databricks postgres generate-database-credential \
  projects/fleet-rlm/branches/production/endpoints/primary \
  --profile <PROFILE> -o json
# token is at: .token
```

## 5. Set `FLEET_DATABASE_URL`

Native role (durable — preferred):

```dotenv
FLEET_DATABASE_URL=postgresql://fleet_app:<password>@<lakebase-host>:5432/fleet_rlm?sslmode=require
```

OAuth identity (short-lived):

```dotenv
FLEET_DATABASE_URL=postgresql://<user-email>:<token>@<lakebase-host>:5432/fleet_rlm?sslmode=require
```

Place the chosen value in an untracked `.env` or configured secret store;
percent-encode reserved characters in usernames and passwords. The database
must already exist: Fleet's migrations create tables, not a PostgreSQL database.
`<lakebase-host>` is the endpoint host from step 1. For the managed profile,
also populate the TOML-declared MLflow values in `.env`:

```dotenv
FLEET_MLFLOW_EXPERIMENT_NAME=fleet-rlm
FLEET_MLFLOW_TRACE_CATALOG=<unity-catalog>
FLEET_MLFLOW_TRACE_SCHEMA=<trace-schema>
FLEET_MLFLOW_TRACE_TABLE_PREFIX=fleet_rlm
FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID=<warehouse-id>
```

`DATABRICKS_HOST` and `DATABRICKS_TOKEN` must authenticate the
Databricks MLflow client when a local policy routes traces to Databricks. Then
initialize the schema:

```bash
uv run python scripts/db_init.py
uv run alembic check
```

## One-time SQLite import

For a maintenance-window cutover, stop Fleet writes first, then use the
operator-only import command with environment-variable names rather than URLs.
It creates an exclusive SQLite backup, applies the Alembic chain to an empty
Lakebase target, copies canonical Fleet rows in foreign-key-safe order, and
retains only counts and deterministic digests in the receipt.

```bash
FLEET_LIVE=1 uv run python scripts/migrate_sqlite_to_postgres.py \
  --maintenance-window \
  --source-url-env FLEET_SQLITE_SOURCE_URL \
  --target-url-env FLEET_DATABASE_URL \
  --backup .scratch/fleet-pre-cutover.sqlite3 \
  --receipt .scratch/fleet-sqlite-to-postgres.json
```

The target URL must use the durable `fleet_app` role and TLS. The command
rejects populated targets, missing canonical source tables, existing receipts,
and failed verification. Keep the verified backup and previous deployment
artifact for the rollback window; there is no bidirectional synchronization.

## Notes and troubleshooting

- **Always `sslmode=require`** — Lakebase rejects non-TLS connections.
- **Scale-to-zero**: cold connection latency varies by deployment. Fleet uses
  pool pre-ping, 1,800-second connection recycling, and a 30-second connection
  timeout; these are client controls, not a provider latency guarantee.
- **Idle/lifetime**: connections idle ~24h are closed; long queries can also
  hit token expiry (OAuth path). Prefer the durable `fleet_app` role for
  servers.
- **`permission denied for schema`**: verify the migration role's schema
  privileges and ownership. Runtime DML and migration DDL have different
  requirements; grant only the permissions needed for each role.
- Token refresh and off-platform connection detail: Databricks Lakebase docs
  on connectivity.

See also: [Database](../reference/database.md) for the runtime Run Environment
model and the canonical table list.
