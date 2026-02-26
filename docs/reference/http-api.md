# HTTP and WebSocket API Reference

This reference describes the server surface exposed by `src/fleet_rlm/server/main.py`.

## Base Surface

- Health endpoints are unprefixed: `/health`, `/ready`
- API endpoints are prefixed with `/api/v1`
- WebSocket endpoints are served under `/api/v1/ws/*`

## Authentication Model

Current auth configuration is controlled by environment/runtime settings:

- `AUTH_MODE=dev|entra` (default `dev`)
- `AUTH_REQUIRED=true|false`
- `ALLOW_DEBUG_AUTH`
- `ALLOW_QUERY_AUTH_TOKENS`

`dev` mode supports debug headers and local HS256 tokens.
`entra` mode is scaffolded and currently fail-closed until JWKS verification is implemented.

See [Auth Modes](../auth.md) for complete behavior and guardrails.

## REST Endpoints (from `openapi.yaml`)

### Health
- `GET /health`
- `GET /ready`

### Auth
- `POST /api/v1/auth/login` (stub)
- `POST /api/v1/auth/logout` (stub)
- `GET /api/v1/auth/me` (stub)

### Chat
- `POST /api/v1/chat`

`POST /api/v1/chat` request body:

```json
{
  "message": "Summarize this file",
  "docs_path": "README.md",
  "trace": false
}
```

Response is a runner payload from `arun_react_chat_once` (dynamic shape; commonly includes `assistant_response`, optional trajectory metadata, turn counters, and warnings).

### Runtime Settings and Diagnostics
- `GET /api/v1/runtime/settings`
- `PATCH /api/v1/runtime/settings`
- `POST /api/v1/runtime/tests/modal`
- `POST /api/v1/runtime/tests/lm`
- `GET /api/v1/runtime/status`

Notes:
- Runtime writes (`PATCH /runtime/settings`) are allowed only when `APP_ENV=local`.
- Tests/status endpoints are readable in all environments.

### Legacy SQLite Compatibility Routes

These route groups are compatibility surfaces and are gated by `LEGACY_SQLITE_ROUTES_ENABLED`.
When disabled, handlers return `410 Gone` with guidance to Neon-backed runtime paths.

- Tasks:
  - `POST /api/v1/tasks`
  - `GET /api/v1/tasks`
  - `GET /api/v1/tasks/{task_id}`
  - `PATCH /api/v1/tasks/{task_id}`
  - `DELETE /api/v1/tasks/{task_id}`
- Sessions:
  - `GET /api/v1/sessions/state` (always available in current router)
  - `POST /api/v1/sessions`
  - `GET /api/v1/sessions`
  - `GET /api/v1/sessions/{session_id}`
  - `PATCH /api/v1/sessions/{session_id}`
  - `DELETE /api/v1/sessions/{session_id}`

Example task payloads use camelCase where defined by schema aliases:

```json
{
  "objective": "Run end-to-end validation",
  "sessionId": "abc123"
}
```

### Planned/Stub Route Groups

These are currently minimal placeholder endpoints and should be treated as scaffolded API surfaces:

- `GET /api/v1/taxonomy`
- `GET /api/v1/taxonomy/{path}`
- `GET /api/v1/analytics`
- `GET /api/v1/analytics/skills/{skill_id}`
- `GET /api/v1/search`
- `GET /api/v1/memory`
- `POST /api/v1/memory`
- `GET /api/v1/sandbox`
- `GET /api/v1/sandbox/file`

## WebSocket Endpoints

WebSockets are intentionally documented from router code (`ws.py`) because they are not represented in OpenAPI.

### `WS /api/v1/ws/chat`

Primary interactive chat stream.

Incoming payload shape (`WSMessage`):

```json
{
  "type": "message",
  "content": "Analyze this document",
  "docs_path": null,
  "trace": true,
  "trace_mode": "compact",
  "workspace_id": "default",
  "user_id": "anonymous",
  "session_id": "session-123"
}
```

Supported `type` values:
- `message`
- `cancel`
- `command`

For `command`, include:

```json
{
  "type": "command",
  "command": "load_document",
  "args": {"path": "README.md", "alias": "active"},
  "session_id": "session-123"
}
```

Server emits envelopes such as:
- `{"type":"event","data":...}`
- `{"type":"command_result","command":"...","result":{...}}`
- `{"type":"error","message":"..."}`

### `WS /api/v1/ws/execution`

Execution graph stream for observability/artifact consumers.

Query params:
- `workspace_id`
- `user_id`
- `session_id` (required; missing session id yields error + close)

Event types:
- `execution_started`
- `execution_step`
- `execution_completed`

## Contract Verification

Use these checks when updating API docs:

```bash
# REST contract
rg -n "^  /" openapi.yaml

# WebSocket routes (not in OpenAPI)
rg -n "@router.websocket" src/fleet_rlm/server/routers/ws/api.py
```
