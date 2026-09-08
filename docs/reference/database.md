# Database

Canonical Run Environment set: `daytona`.

Set a Daytona profile in `config/fleet.toml` before starting this backend. The
committed profiles and their provider environment names are listed in the
[profile matrix](profile-matrix.md):

| Profile | Code execution | LLM calls | Durable volume | Auth/scope |
| --- | --- | --- | --- | --- |
| `daytona-recursive` (default) | Daytona Sandbox Code Interpreter | real `dspy.LM` | Workspace Volume | local scope |

Daytona is the full Fleet solution with Workspace Volume Scope and Turn Commit
promotion. Private deterministic tests use an in-memory composition and do not
represent another public runtime profile.

Alembic owns the baseline and incremental revisions under `migrations/versions/`.
For a new database, apply the full chain; for an existing deployment, review
the pending migrations and back up the database before upgrading. Startup checks
compatibility and never upgrades automatically.

```bash
export FLEET_DATABASE_URL='postgresql+asyncpg://...'
uv run python scripts/db_init.py
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
profile names a different database variable. Keep both targets aligned; see
the [migration README](../../migrations/README.md). MLflow owns a separate
tracking schema and has its own [upgrade procedure](cli.md#upgrade-the-local-mlflow-store-to-316).
