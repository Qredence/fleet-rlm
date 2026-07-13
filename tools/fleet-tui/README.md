# Fleet RLM Terminal UI

This is a local Node 22+ terminal client for Fleet RLM's existing FastAPI SSE
API. It uses `ai` and `@ai-sdk/tui` to render the backend's AI SDK UI v1
stream; it does not run a local model, a Harness agent, or any Vercel Sandbox.

## Run

Start the Fleet API in a separate terminal with the local hermetic SQLite
environment. This path needs no LLM or Daytona credentials.

```bash
# from repository root; model names are examples for an OpenAI-compatible endpoint
FLEET_AUTH_MODE=dev \
FLEET_RUN_ENVIRONMENT=hermetic \
FLEET_DATABASE_URL='sqlite+aiosqlite:///./.fleet_rlm/local.sqlite3' \
uv run fleet-rlm serve-api --port 8000

# in another terminal
pnpm --dir tools/fleet-tui start
```

For real Fleet RLM execution through Daytona and `dspy.RLM`, start the API
against a freshly migrated disposable PostgreSQL database with provider
credentials:

```bash
FLEET_AUTH_MODE=dev \
FLEET_RUN_ENVIRONMENT=daytona \
FLEET_DATABASE_URL='postgresql+asyncpg://...' \
FLEET_DAYTONA_API_KEY='...' \
FLEET_LLM_API_KEY='...' \
FLEET_ROOT_MODEL='openai/<model>' \
FLEET_SUB_MODEL='openai/<model>' \
uv run alembic upgrade head

FLEET_AUTH_MODE=dev \
FLEET_RUN_ENVIRONMENT=daytona \
FLEET_DATABASE_URL='postgresql+asyncpg://...' \
FLEET_DAYTONA_API_KEY='...' \
FLEET_LLM_API_KEY='...' \
FLEET_ROOT_MODEL='openai/<model>' \
FLEET_SUB_MODEL='openai/<model>' \
uv run fleet-rlm serve-api --port 8000
```

Run the TUI in another terminal with `pnpm --dir tools/fleet-tui start`. The
SQLite/hermetic command above is only for local transport/UI smoke testing.

The command creates a Fleet session and prints its UUID. Resume server-side
conversation context later with:

```bash
pnpm --dir tools/fleet-tui start -- --session <session-uuid>
```

For non-default synthetic development identities, supply both headers:

```bash
pnpm --dir tools/fleet-tui start -- \
  --user-id <user-uuid> \
  --workspace-id <workspace-uuid>
```

Use `--api-url <url>` to point the client at another dev API. `FLEET_ROOT_MODEL`
and `FLEET_SUB_MODEL` are the canonical runtime settings; for an OpenAI-compatible
endpoint they must use the `openai/<model-id>` form. Neon/Bearer JWT
authentication is intentionally out of scope for this first version.
`FLEET_BUDGET_MAX_WALL_SECONDS` controls the maximum wall-clock duration of one
live RLM turn and defaults to 900 seconds.

When resuming, the client first restores the durable server-side transcript:
user and assistant text, sanitized RLM reasoning, tool calls/results, and
Fleet trajectory data such as RLM code/output, artifacts, skills, and usage.
The stock TUI then starts a new interactive display for subsequent turns.
Sensitive fields in restored structured data are redacted defensively.

## Validate

```bash
pnpm --dir tools/fleet-tui install --frozen-lockfile
pnpm --dir tools/fleet-tui run format:check
pnpm --dir tools/fleet-tui run lint
pnpm --dir tools/fleet-tui run typecheck
pnpm --dir tools/fleet-tui run test
```
