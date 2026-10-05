# Database

Canonical Run Environment set: `daytona`.

The single `config/fleet.toml` policy selects Daytona sandbox execution,
stock `dspy.LM` calls, and a durable Session Workspace Volume. See the
[environment reference](configuration-environment.md) for its declared inputs.
Startup requires the configured database URL at Alembic head; SQLite and
PostgreSQL are supported. Explicit Lakebase preflight enforces the production
TLS and `fleet_app` role requirements.

Private deterministic tests use an in-memory composition and do not represent
another public runtime environment.

Alembic owns the baseline and incremental revisions under `migrations/versions/`.
For a new database, apply the full chain; for an existing deployment, review
the pending migrations and back up the database before upgrading. Startup checks
compatibility and never upgrades automatically.

```bash
export FLEET_DATABASE_URL='postgresql+asyncpg://...'
uv run python scripts/database.py upgrade
uv run alembic check
```

The canonical tables are `fleet_users`, `fleet_workspaces`, `fleet_sessions`,
`fleet_turns`, `fleet_runs`, `fleet_sandbox_bindings`, `fleet_attachments`, `fleet_artifacts`,
`fleet_skills`, and `fleet_memory_promotion_intents` (the autonomous-memory
promotion outbox). SQLAlchemy models live in `fleet_rlm.persistence.models`.

Production startup assumes migrations have already run. Explicit SQLite
test/offline helpers may call `create_tables`; all other environments must use
Alembic.

Migration tooling reads `FLEET_DATABASE_URL` directly, even if a custom runtime
configuration names a different database variable. Keep both targets aligned; see
the [migration README](../../migrations/README.md). MLflow owns a separate
tracking schema and has its own [upgrade procedure](cli.md#upgrade-the-local-mlflow-store-to-316).
