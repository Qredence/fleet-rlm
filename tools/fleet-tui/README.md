# Fleet RLM Terminal UI

This is a local Node 22+ terminal client for Fleet RLM's existing FastAPI SSE
API. It uses Ink to render the backend's AI SDK UI v1
stream; it does not run a local model, a Harness agent, or any Vercel Sandbox.

## Run

Start the Fleet API in a separate terminal with the local Deno SQLite
environment. This path needs an LLM key but no Daytona credentials.

```bash
# from repository root; model names are examples for an OpenAI-compatible endpoint
FLEET_RUN_ENVIRONMENT=deno \
FLEET_DATABASE_URL='sqlite+aiosqlite:///./.fleet_rlm/local.sqlite3' \
FLEET_LLM_API_KEY='...' \
uv run fleet-rlm serve-api --port 8000

# in another terminal
pnpm --dir tools/fleet-tui start
```

For real Fleet RLM execution through Daytona and `dspy.RLM`, migrate the
database, then use the combined backend-and-Ink command:

```bash
FLEET_DATABASE_URL='postgresql+asyncpg://...' \
FLEET_DAYTONA_API_KEY='...' \
FLEET_LLM_API_KEY='...' \
FLEET_ROOT_MODEL='openai/<model>' \
FLEET_SUB_MODEL='openai/<model>' \
uv run alembic upgrade head

FLEET_DATABASE_URL='postgresql+asyncpg://...' \
FLEET_DAYTONA_API_KEY='...' \
FLEET_LLM_API_KEY='...' \
FLEET_ROOT_MODEL='openai/<model>' \
FLEET_SUB_MODEL='openai/<model>' \
uv run fleet cli --port 8000
```

The supervised backend logs to `.fleet_rlm/logs/latest.log` and stops when Ink
exits. Run `uv run fleet doctor daytona` first when validating provider access
or diagnosing Sandbox creation.

For local Deno/Pyodide execution, configure the LLM settings and run
`uv run fleet deno --port 8000`; this mode intentionally has no durable
Artifact promotion.

The command creates a Fleet session and prints its UUID. Resume server-side
conversation context later with:

```bash
uv run fleet cli -- --session <session-uuid>
```

Use `--api-url <url>` to point the client at another dev API. `FLEET_ROOT_MODEL`
and `FLEET_SUB_MODEL` are the canonical runtime settings; for an OpenAI-compatible
endpoint they must use the `openai/<model-id>` form. The API uses one
deterministic local User and Workspace scope.
`FLEET_BUDGET_MAX_WALL_SECONDS` controls the maximum wall-clock duration of one
live RLM turn and defaults to 900 seconds.

When resuming, the client atomically hydrates the Ink store with durable user
and assistant text, sanitized RLM reasoning, tool calls/results, and Fleet
trajectory data such as RLM code/output, structured results, artifacts, skills,
and usage.

## Operator timeline

Ink renders one white-and-gray execution timeline with no semantic color
dependency. Reasoning, code, interpreter output, tools, errors, Result, and
usage remain chronological. The timeline grows upward from the prompt and
follows new output at the bottom. Execution cards start expanded; focus a card
and press `Enter` or `Space` to collapse it. Code is shown without a line-number
gutter. Use `PageUp` and `PageDown` to scroll, and `End` to jump to the bottom
and resume live-follow. Keyboard help sits below the input border. HTML
character references are decoded for readability while HTML tags remain inert
text.

## Validate

```bash
pnpm --dir tools/fleet-tui install --frozen-lockfile
pnpm --dir tools/fleet-tui run format:check
pnpm --dir tools/fleet-tui run lint
pnpm --dir tools/fleet-tui run typecheck
pnpm --dir tools/fleet-tui run test
```
