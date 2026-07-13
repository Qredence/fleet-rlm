# Fleet RLM Terminal UI

This is a local Node 22+ terminal client for Fleet RLM's existing FastAPI SSE
API. It uses `ai` and `@ai-sdk/tui` to render the backend's AI SDK UI v1
stream; it does not run a local model, a Harness agent, or any Vercel Sandbox.

## Run

Start the Fleet API in a separate terminal with the live kernel enabled. The
configured LLM endpoint, provider-qualified model IDs, and Daytona API key are
required; without `FLEET_LIVE_KERNEL=true`, Fleet starts an offline runtime that
cannot execute RLM turns.

```bash
# from repository root; model names are examples for an OpenAI-compatible endpoint
FLEET_AUTH_MODE=dev \
FLEET_LIVE_KERNEL=true \
FLEET_MAX_TURN_WALL_SECONDS=900 \
FLEET_ROOT_MODEL=openai/deepseek-v4-flash-free \
FLEET_SUB_MODEL=openai/deepseek-v4-flash-free \
uv run fleet-rlm serve-api --port 8000

# in another terminal
pnpm --dir tools/fleet-tui start
```

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
`FLEET_MAX_TURN_WALL_SECONDS` controls the maximum wall-clock duration of one
live RLM turn and defaults to 900 seconds.

When resuming, the client first restores the durable server-side transcript:
user and assistant text, sanitized RLM reasoning, tool calls/results, and
Fleet trajectory data such as RLM code/output, artifacts, skills, and usage.
The stock TUI then starts a new interactive display for subsequent turns.
Sensitive fields in restored structured data are redacted defensively.

## Validate

```bash
pnpm --dir tools/fleet-tui test
pnpm --dir tools/fleet-tui typecheck
```
