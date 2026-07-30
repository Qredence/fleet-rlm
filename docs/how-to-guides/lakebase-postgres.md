# Lakebase Postgres

How to provision a Databricks Lakebase (Postgres Autoscaling) database for Fleet
RLM and point the TOML-declared `FLEET_DATABASE_URL` at it. The local
`daytona` profile keeps its supervised MLflow server; use
`default_profile = "daytona-managed"` in `config/fleet.toml` when the same
deployment should route traces to managed Databricks MLflow.

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

Fleet RLM connects over asyncpg with `sslmode=require`. Alembic owns the schema
(`migrations/`); `scripts/db_init.py` upgrades a fresh database to head.

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
```

Grant ownership/membership as your deployment requires. **Never commit the
password.**

## 4. Alternative: short-lived OAuth token

For quick sessions you can skip the native role and use an OAuth token for a
Databricks identity. Tokens expire after ~1 hour and must be refreshed.

```bash
databricks postgres generate-database-credential \
  projects/fleet-rlm/branches/production/endpoints/primary \
  --profile <PROFILE> -o json
# token is at: .token
```

## 5. Set `FLEET_DATABASE_URL`

Native role (durable — preferred):

```bash
FLEET_DATABASE_URL=postgresql://fleet_app:<password>@<lakebase-host>:5432/fleet_rlm?sslmode=require
```

OAuth identity (short-lived):

```bash
FLEET_DATABASE_URL=postgresql://<user-email>:<token>@<lakebase-host>:5432/fleet_rlm?sslmode=require
```

`<lakebase-host>` is the endpoint host from step 1. For the managed profile,
also populate the TOML-declared MLflow values in `.env`:

```dotenv
FLEET_MLFLOW_EXPERIMENT_NAME=fleet-rlm
FLEET_MLFLOW_TRACE_CATALOG=<unity-catalog>
FLEET_MLFLOW_TRACE_SCHEMA=<trace-schema>
FLEET_MLFLOW_TRACE_TABLE_PREFIX=fleet_rlm
FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID=<warehouse-id>
```

`DATABRICKS_HOST` and `DATABRICKS_TOKEN` must authenticate the managed
Databricks MLflow client; `DATABRICKS_TOKEN` is also the TOML-declared Root/Sub
AI Gateway credential for the managed profile. Then initialize the schema:

```bash
uv run python scripts/db_init.py
uv run alembic check
```

## Notes and troubleshooting

- **Always `sslmode=require`** — Lakebase rejects non-TLS connections.
- **Scale-to-zero**: endpoints suspend when idle and wake in ~100 ms; the
  runtime enables pool pre-ping and connection recycle to tolerate this.
- **Idle/lifetime**: connections idle ~24h are closed; long queries can also
  hit token expiry (OAuth path). Prefer the durable `fleet_app` role for
  servers.
- **`permission denied for schema`**: the role must own the schema. Create the
  schema as `fleet_app` (or grant membership) rather than as your user.
- Token refresh and off-platform connection detail: Databricks Lakebase docs
  on connectivity.

See also: [Database](../reference/database.md) for the runtime Run Environment
model and the canonical table list.
