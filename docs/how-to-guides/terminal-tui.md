# Use the Fleet terminal UI

`tools/fleet-tui/` is a standalone Node 22+ development client for the
canonical FastAPI `POST /api/chat` SSE endpoint. It adapts the backend's AI SDK
UI v1 chunks into the `Agent.fullStream` consumed by `@ai-sdk/tui`.

The client has no local model, provider key, Harness agent, or sandbox. Fleet
remains responsible for RLM execution, tools, Daytona, session persistence, and
disconnect cancellation.

Start the backend with dev authentication and the live kernel enabled. This
requires configured LLM and Daytona credentials; the model values below are an
example for an OpenAI-compatible endpoint. Then from the repository root run:

```bash
FLEET_AUTH_MODE=dev \
FLEET_LIVE_KERNEL=true \
FLEET_MAX_TURN_WALL_SECONDS=900 \
FLEET_ROOT_MODEL=openai/deepseek-v4-flash-free \
FLEET_SUB_MODEL=openai/deepseek-v4-flash-free \
uv run fleet-rlm serve-api --port 8000

# in another terminal
pnpm --dir tools/fleet-tui start
```

The tool creates a durable Fleet session and prints its UUID. To continue its
backend conversation context from a new terminal process:

```bash
pnpm --dir tools/fleet-tui start -- --session <session-uuid>
```

Optional `--user-id` and `--workspace-id` flags add synthetic `X-Fleet-*`
headers for dev mode. The first version deliberately does not support Neon JWT
authentication. On resume, the client prints the persisted transcript before
opening the interactive TUI: text, sanitized reasoning, tool calls/results,
and Fleet trajectory parts (RLM code/output, artifacts, skills, and usage).
Sensitive values in structured restored data are redacted.

The terminal client does not host a model or a sandbox. It only consumes the
FastAPI SSE stream; the live server performs every model call and Daytona
interpreter operation. `FLEET_ROOT_MODEL` and `FLEET_SUB_MODEL` are the
canonical model settings; for an OpenAI-compatible base URL, use
`openai/<model-id>` rather than provider-specific environment aliases.
`FLEET_MAX_TURN_WALL_SECONDS` defaults to 900 seconds and can be raised for
long-running research turns.

The stock TUI renders text, sanitized reasoning, and backend-executed tool
events while a run is active. Its `data-*` chunks are retained by FastAPI and
shown when the session is restored, rather than discarded by the terminal
client.
