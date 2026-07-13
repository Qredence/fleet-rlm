# Database

Local development uses a hermetic SQLite database:

```bash
export FLEET_RUN_ENVIRONMENT=hermetic
export FLEET_DATABASE_URL='sqlite+aiosqlite:///./.fleet_rlm/local.sqlite3'
```

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

Production startup assumes migrations have already run. Test and offline SQLite
helpers may call `create_tables` explicitly.
