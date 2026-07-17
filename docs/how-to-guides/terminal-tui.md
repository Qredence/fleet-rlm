# Use the Fleet terminal UI

`tools/fleet-tui/` is a standalone Node 22.19+ development client for the
canonical FastAPI `POST /api/sessions/{session_id}/turns` SSE endpoint. It
projects the backend's AI SDK UI v1 chunks into the maintained pi-tui store.

The client has no local model, provider key, Harness agent, or sandbox. Fleet
remains responsible for RLM execution, tools, Daytona, session persistence, and
disconnect cancellation.

Start the combined backend and terminal with Daytona enabled. This requires
configured LLM and Daytona credentials; the model values below are an
example for an OpenAI-compatible endpoint. Initialize a fresh configured
database once before the first Daytona launch:

```bash
uv run python scripts/db_init.py
```

`fleet cli` checks the Alembic revision before starting the backend or pi-tui and
prints that recovery command when the database is empty or stale; it never
applies migrations automatically. Then from the repository root run:

```bash
FLEET_TURN_TIMEOUT_SECONDS=900 \
FLEET_ROOT_MODEL=openai/deepseek-v4-flash-free \
FLEET_SUB_MODEL=openai/deepseek-v4-flash-free \
uv run fleet cli --port 8000
```

The tool creates a durable Fleet session and prints its UUID. To continue its
backend conversation context from a new terminal process:

```bash
uv run fleet cli -- --session <session-uuid>
```

The backend assigns the deterministic local User and Workspace scope. On
resume, the client atomically hydrates the renderer-neutral store with
persisted text, sanitized reasoning, tool calls/results, and Fleet trajectory
parts such as RLM code/output, structured results, artifacts, skills, and
usage.

The screen is an achromatic execution timeline. Reasoning, code, interpreter
output, tools, recoverable errors, and the final Result appear in stream order.
Messages remain fully expanded and untruncated. The transcript, activity,
editor, and footer form one native-scrollback history; Fleet never captures the
mouse, pins the prompt, or clips old evidence. Use the terminal's wheel,
trackpad, or `Shift+PageUp/PageDown` shortcuts to inspect earlier output. Plain
`PageUp` and `PageDown` remain available to the editor. Resize or hydration may
replay the transcript and return to the live bottom.

Use `/skills` to list discoverable Skill Cards. `/skill <name-or-id>` pins the
current discoverable version for the next accepted Turn, while
`/skill <hidden-uuid>@<version>` pins an explicit-only Skill without listing or
otherwise exposing it. `/skill clear` clears all pending selections. At most
four unique Skills may be pending. The terminal clears them once the Turn
stream opens; a pre-header validation or network failure keeps them so the
request can be corrected or retried.

The terminal client does not host a model or a sandbox. It only consumes the
FastAPI SSE stream; the server performs every model call and either Daytona
Sandbox execution or local Deno/Pyodide execution, according to the selected
Run Environment. `FLEET_ROOT_MODEL` and `FLEET_SUB_MODEL` are the canonical
model settings; for an OpenAI-compatible base URL, use
`openai/<model-id>` rather than provider-specific environment aliases.
`FLEET_TURN_TIMEOUT_SECONDS` configures the Turn Timeout, defaults to 900
seconds, and can be raised for long-running research turns.

Use `uv run fleet deno --port 8000` instead to run the same terminal with
DSPy's local Deno/Pyodide interpreter and no durable Artifact promotion. Use
`fleet web` or `fleet-rlm serve-api` plus the standalone pnpm command only when
you intentionally want backend and pi-tui in separate terminals. Supervised
backend logs are available at `.fleet_rlm/logs/latest.log`; correlated public
Turn failures display the request id needed to find the matching safe log line.

Before diagnosing a Daytona Turn, run `uv run fleet doctor daytona`. This
opt-in check creates and deletes one disposable scoped Sandbox and reports a
sanitized category if authentication, quota, request, network, mount, or
interpreter validation fails.
