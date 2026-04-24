# Environment

Environment variables, external dependencies, and setup notes.

**What belongs here:** Required env vars, external API keys/services, dependency quirks.
**What does NOT belong here:** Service ports/commands (use `.factory/services.yaml`).

---

## Python Environment
- Python 3.13.9, managed via `uv`
- DSPy 3.1.3 pinned in pyproject.toml
- All extras: `uv sync --all-extras`

## Key Env Vars
- `DAYTONA_API_KEY`, `DAYTONA_API_URL` — Daytona sandbox access
- `DATABASE_URL` — pooled Neon runtime connection
- `DATABASE_ADMIN_URL` — direct Neon for Alembic/admin
- `FLEET_RLM_LOCAL_DB_URL` — SQLite sidecar override (default: `.data/local.db`)
- `APP_ENV` — `local` enables settings PATCH
- `AUTH_MODE` — `dev` or `entra`

## DSPy 3.1.3 Notes
- No `@dspy.tool` decorator — tools are plain callables or `dspy.Tool(func)` wrappers
- `dspy.ReAct(signature, tools=[...])` accepts list of callables
- `dspy.History` is a frozen Pydantic model
- `dspy.streamify(program)` returns async generator
