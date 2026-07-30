# Database

Canonical Run Environment set: `deno`, `daytona`.

Local compatibility development can use Deno with SQLite:

```bash
export FLEET_DATABASE_URL='sqlite+aiosqlite:///./.fleet_rlm/local.sqlite3'
```

Set `[config] default_profile = "local-deno"` in `config/fleet.toml` before
starting this backend. The committed profiles provide these Run policies:

| Profile | Code execution | LLM calls | Durable volume | Auth/scope |
| --- | --- | --- | --- | --- |
| `local-deno` | DSPy default `PythonInterpreter` (Deno + Pyodide WASM) | real `dspy.LM` | none | local scope |
| `daytona` (default) | Daytona Sandbox Code Interpreter | real `dspy.LM` | Workspace Volume | local scope |

`deno` is intentional vanilla local `dspy.RLM`: real LLM calls and in-process
attachment/skill tools, but no `create_artifact`, no Artifact Candidate
promotion, and no Daytona broker. `daytona` is the full Fleet solution with
Workspace Volume Scope and Turn Commit promotion.

The Deno mode is additive: it issues real LLM requests and runs real Python
inside the local Deno/Pyodide WASM interpreter, but never touches the Daytona
broker or Workspace Volume. Its in-process sinks are Run-scoped working state,
not a durable volume or an Artifact publication path.

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

Production startup assumes migrations have already run. Private testing and
Deno SQLite helpers may call `create_tables` explicitly.
