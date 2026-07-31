# Database

Canonical Run Environment set: `daytona`.

Set a Daytona profile in `config/fleet.toml` before starting this backend. The
committed profiles provide these Run policies:

| Profile | Code execution | LLM calls | Durable volume | Auth/scope |
| --- | --- | --- | --- | --- |
| `daytona` (default) | Daytona Sandbox Code Interpreter | real `dspy.LM` | Workspace Volume | local scope |

Daytona is the full Fleet solution with Workspace Volume Scope and Turn Commit
promotion. Private deterministic tests use an in-memory composition and do not
represent another public runtime profile.

For disposable PostgreSQL or production, Fleet RLM starts from an empty
database and one Alembic baseline under `migrations/versions/`.

```bash
export FLEET_DATABASE_URL='postgresql+asyncpg://...'
uv run python scripts/db_init.py
uv run alembic check
```

The canonical tables are `fleet_users`, `fleet_workspaces`, `fleet_sessions`,
`fleet_turns`, `fleet_runs`, `fleet_sandbox_bindings`, `fleet_attachments`, `fleet_artifacts`, and
`fleet_skills`. SQLAlchemy models live in `fleet_rlm.persistence.models`.

Production startup assumes migrations have already run. Explicit SQLite
test/offline helpers may call `create_tables`; all other environments must use
Alembic.
