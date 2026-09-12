# Fleet database migrations

Alembic owns Fleet's schema for PostgreSQL deployments and explicit local
SQLite databases. It is independent of the MLflow tracking database.

From the repository root, set `FLEET_DATABASE_URL` to the same database selected
by the runtime policy, then run:

```bash
uv run python scripts/db_init.py
uv run alembic check
```

The initializer applies the migration chain to head. Backend startup checks
compatibility and never applies migrations. Review pending revisions and retain
a verified backup before upgrading an existing database. Do not use test-only
table creation helpers to evolve a deployment.

The initializer and Alembic read `FLEET_DATABASE_URL` directly; they do not
resolve custom TOML environment-reference names. If the application policy uses
another variable name, provide the same target through `FLEET_DATABASE_URL`
for migrations. Never put a credential-bearing URL in shell history.

See the [database reference](../docs/reference/database.md) for tables and
the [testing strategy](../docs/how-to-guides/testing-strategy.md) for explicit
database validation lanes.
