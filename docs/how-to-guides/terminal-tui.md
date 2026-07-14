# Use the Fleet terminal UI

`tools/fleet-tui/` is a standalone Node 22+ development client for the
canonical FastAPI `POST /api/sessions/{session_id}/turns` SSE endpoint. It
projects the backend's AI SDK UI v1 chunks into the maintained Ink store.

The client has no local model, provider key, Harness agent, or sandbox. Fleet
remains responsible for RLM execution, tools, Daytona, session persistence, and
disconnect cancellation.

Start the combined backend and terminal with dev authentication and the live kernel enabled. This
requires configured LLM and Daytona credentials; the model values below are an
example for an OpenAI-compatible endpoint. Then from the repository root run:

```bash
FLEET_AUTH_MODE=dev \
FLEET_BUDGET_MAX_WALL_SECONDS=900 \
FLEET_ROOT_MODEL=openai/deepseek-v4-flash-free \
FLEET_SUB_MODEL=openai/deepseek-v4-flash-free \
uv run fleet cli --port 8000
```

The tool creates a durable Fleet session and prints its UUID. To continue its
backend conversation context from a new terminal process:

```bash
uv run fleet cli -- --session <session-uuid>
```

Optional `--user-id` and `--workspace-id` flags add synthetic `X-Fleet-*`
headers for dev mode. The first version deliberately does not support Neon JWT
authentication. On resume, the client atomically hydrates the Ink store with
persisted text, sanitized reasoning, tool calls/results, and Fleet trajectory
parts such as RLM code/output, structured results, artifacts, skills, and
usage.

The screen is an achromatic execution timeline. Reasoning, code, interpreter
output, tools, recoverable errors, and the final Result appear in stream order.
Cards start expanded. Use `Tab` or the documented thread-navigation keys to
move focus, `Enter` or `Space` to collapse or expand the focused card,
`PageUp`/`PageDown` to inspect older output, and `End` to return to live-follow
mode at the bottom. New messages grow upward from the fixed prompt, execution
events have a single separating row, and code is displayed without line
numbers. Keyboard help is rendered below the input border; the prompt and run
status remain fixed while the timeline scrolls.

The terminal client does not host a model or a sandbox. It only consumes the
FastAPI SSE stream; the server performs every model call and either Daytona
Sandbox execution or local Deno/Pyodide execution, according to the selected
Run Environment. `FLEET_ROOT_MODEL` and `FLEET_SUB_MODEL` are the canonical
model settings; for an OpenAI-compatible base URL, use
`openai/<model-id>` rather than provider-specific environment aliases.
`FLEET_BUDGET_MAX_WALL_SECONDS` defaults to 900 seconds and can be raised for
long-running research turns.

Use `uv run fleet deno --port 8000` instead to run the same terminal with
DSPy's local Deno/Pyodide interpreter and no durable Artifact promotion. Use
`fleet web` or `fleet-rlm serve-api` plus the standalone pnpm command only when
you intentionally want backend and Ink in separate terminals. Supervised
backend logs are available at `.fleet_rlm/logs/latest.log`; correlated public
Turn failures display the request id needed to find the matching safe log line.

Before diagnosing a Daytona Turn, run `uv run fleet doctor daytona`. This
opt-in check creates and deletes one disposable scoped Sandbox and reports a
sanitized category if authentication, quota, request, network, mount, or
interpreter validation fails.
