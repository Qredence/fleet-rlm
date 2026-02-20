# HTTP API Reference

The `fleet-rlm` server exposes both conversational and task-oriented endpoints via FastAPI.

## Authentication

- `AUTH_MODE=dev`:
  - Default behavior is `AUTH_REQUIRED=false` (auth optional).
  - Optional fallback identity when auth is omitted/invalid:
    - `tenant_claim=default` (or `ws_default_workspace_id`)
    - `user_claim=anonymous` (or `ws_default_user_id`)
  - Debug headers (`X-Debug-Tenant-Id`, `X-Debug-User-Id`, `X-Debug-Email`, `X-Debug-Name`), or
  - `Authorization: Bearer <HS256 token>` with `tid`/`oid`/`email`/`name`.
  - WebSocket-only fallback query auth: `debug_tenant_id` + `debug_user_id` (optional `debug_email`/`debug_name`) or `access_token=<HS256 token>`.
  - Set `AUTH_REQUIRED=true` to enforce auth on all non-health HTTP + all WS routes.
- `AUTH_MODE=entra`: scaffolded and currently fail-closed until JWKS validation wiring is added.

Identity is normalized to:

- `tenant_claim` (`tid`)
- `user_claim` (`oid`)
- `email`
- `name`

## Chat Endpoints

### `POST /api/v1/chat`

Run a single ReAct chat turn.

**Request:**

```json
{
  "message": "Calculate the factorial of 500",
  "docs_path": "/data/knowledge/math_docs.txt",
  "trace": false
}
```

**Response:**

```json
{
  "assistant_response": "The text of the agent's answer...",
  "trajectory": {},
  "history_turns": 1,
  "guardrail_warnings": []
}
```

Notes:

- `trajectory` is omitted when `trace=false`.
- `guardrail_warnings` is additive and may be an empty array.

### `WS /api/v1/ws/chat`

WebSocket endpoint for real-time streaming and command dispatch.

**Client -> Server message shapes:**

- Message turn:

```json
{
  "type": "message",
  "content": "...",
  "docs_path": null,
  "trace": true,
  "trace_mode": "compact",
  "workspace_id": "default",
  "user_id": "alice",
  "session_id": "session-123"
}
```

**WebSocket auth-only query params (dev mode):**

- `debug_tenant_id`
- `debug_user_id`
- `debug_email`
- `debug_name`
- `access_token`

- Cancel:

```json
{ "type": "cancel" }
```

- Command dispatch:

```json
{
  "type": "command",
  "command": "write_to_file",
  "args": { "path": "notes/todo.md", "content": "...", "append": true },
  "workspace_id": "default",
  "user_id": "alice",
  "session_id": "session-123"
}
```

**Server -> Client message shapes:**

- Event stream:

```json
{
  "type": "event",
  "data": {
    "kind": "final",
    "text": "assistant response text",
    "payload": {
      "trajectory": {},
      "final_reasoning": "...",
      "history_turns": 3,
      "guardrail_warnings": []
    },
    "timestamp": "...ISO8601..."
  }
}
```

- Command result: `{"type":"command_result","command":"...","result":{}}`
- Error: `{"type":"error","message":"..."}`

Session identity notes:

- Auth claims are canonical tenant/user authority.
- `workspace_id` and `user_id` in payloads are accepted for compatibility but non-authoritative.
- Session cache key is still tracked as `workspace_id:user_id` internally, populated from auth claims.

### `WS /api/v1/ws/execution`

Dedicated execution graph stream for Artifact Canvas and observability clients.

**Subscription query params (required):**

- `workspace_id`
- `user_id`
- `session_id`

If any filter is missing, the server returns an error payload and closes with policy
violation (`1008`).

**Server -> Client `ExecutionEvent` shape:**

```json
{
  "type": "execution_step",
  "run_id": "default:alice:session-123:1",
  "workspace_id": "default",
  "user_id": "alice",
  "session_id": "session-123",
  "step": {
    "id": "default:alice:session-123:1:s3",
    "parent_id": "default:alice:session-123:1:root",
    "type": "tool",
    "label": "load_document",
    "input": { "tool_name": "load_document", "tool_args": "path='docs/a.md'" },
    "output": null,
    "timestamp": 1739916000.123
  }
}
```

Event lifecycle:

- `execution_started` (one per chat turn)
- `execution_step` (live LLM/tool/repl/memory/output graph updates)
- `execution_completed` (terminal event for the run)

Notes:

- `run_id` is deterministic per turn: `{workspace_id}:{user_id}:{session_id}:{turn_index}`.
- Step payload fields are best-effort and sanitized (truncated + sensitive key redaction).
- `/api/v1/ws/chat` remains unchanged and backward compatible.

## Task Endpoints (`/api/v1/tasks/{type}`)

These endpoints wrap specific `fleet_rlm.runners` functions.

### `POST /api/v1/tasks/basic`

Runs the `run_basic` runner loop.

### `POST /api/v1/tasks/architecture`

Runs `run_architecture` (Analysis of docs). Requires `docs_path`.

### `POST /api/v1/tasks/long-context`

Runs the Long-Context RLM strategy.
**Params:**

- `mode`: "analyze" or "summarize" (via `task_type` in body or inferred)

## Schemas

### `TaskRequest`

Common input schema for task endpoints:

```json
{
  "task_type": "basic",
  "question": "string",
  "query": "string (optional)",
  "docs_path": "string (optional)",
  "max_iterations": 15,
  "max_llm_calls": 30,
  "timeout": 600,
  "chars": 10000,
  "verbose": true
}
```

### `TaskResponse`

```json
{
  "ok": true,
  "result": {},
  "error": "string (if ok=false)"
}
```

## Auth Introspection

### `GET /api/v1/auth/me`

Returns normalized identity and, when DB is configured, resolved tenant/user UUIDs.
