# HTTP and WebSocket API Reference

This reference describes the server surface exposed by `src/fleet_rlm/server/main.py`.

## Base Surface

- Health endpoints are unprefixed: `/health`, `/ready`
- API endpoints are prefixed with `/api/v1`
- WebSocket endpoints are served under `/api/v1/ws/*`

## Authentication Model

Configuration controls:

- `AUTH_MODE=dev|entra` (default `dev`)
- `AUTH_REQUIRED=true|false`
- `ALLOW_DEBUG_AUTH`
- `ALLOW_QUERY_AUTH_TOKENS`

`dev` supports debug headers and local HS256 tokens.
`entra` is scaffolded and currently fail-closed until JWKS verification is implemented.

See [Auth Modes](auth.md).

## REST Endpoints (from `openapi.yaml`)

### Health

- `GET /health`
- `GET /ready`

### Auth (stub responses)

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`

Response contracts:

- `POST /api/v1/auth/login` → `{"token": "<string>"}`
- `POST /api/v1/auth/logout` → `{"status": "ok"}`
- `GET /api/v1/auth/me` → identity envelope with `tenant_claim`, `user_claim`, optional `email`, `name`, `tenant_id`, `user_id`

### Chat

- `POST /api/v1/chat`

Compatibility/deprecation policy:

- Canonical product chat interface: `WS /api/v1/ws/chat`
- `POST /api/v1/chat` remains compatibility-only and is deprecated.
- Removal target: `v0.4.93`
- Compatibility responses include:
  - `Deprecation: true`
  - `Link: </api/v1/ws/chat>; rel="successor-version"`
  - `X-Fleet-Removal-Version: 0.4.93`

Request body:

```json
{
  "message": "Summarize this file",
  "docs_path": "README.md",
  "trace": false
}
```

Equivalent WebSocket message frame:

```json
{
  "type": "message",
  "content": "Summarize this file",
  "docs_path": "README.md",
  "trace": false,
  "workspace_id": "default",
  "user_id": "anonymous",
  "session_id": "session-123"
}
```

### Runtime Settings and Diagnostics

- `GET /api/v1/runtime/settings`
- `PATCH /api/v1/runtime/settings`
- `POST /api/v1/runtime/tests/modal`
- `POST /api/v1/runtime/tests/lm`
- `GET /api/v1/runtime/status`

Notes:

- `PATCH /api/v1/runtime/settings` is local-only (`APP_ENV=local`).
- Read/test endpoints remain available across environments.
- Runtime settings model updates are hot-applied in-process before LM rebuild:
  - `DSPY_LM_MODEL` updates planner model selection
  - `DSPY_DELEGATE_LM_MODEL` updates delegate model selection
- `GET /api/v1/runtime/status` includes `active_models`:
  - `active_models.planner`
  - `active_models.delegate`
  - `active_models.delegate_small`

### Legacy SQLite Compatibility Routes

These routes are guarded by `LEGACY_SQLITE_ROUTES_ENABLED`.

- When enabled, they serve compatibility CRUD behavior.
- When disabled, they can return `410 Gone` with migration guidance.

Routes:

- `POST /api/v1/tasks`
- `GET /api/v1/tasks`
- `GET /api/v1/tasks/{task_id}`
- `PATCH /api/v1/tasks/{task_id}`
- `DELETE /api/v1/tasks/{task_id}`
- `GET /api/v1/sessions/state` (always available)
- `POST /api/v1/sessions`
- `GET /api/v1/sessions`
- `GET /api/v1/sessions/{session_id}`
- `PATCH /api/v1/sessions/{session_id}`
- `DELETE /api/v1/sessions/{session_id}`

### Planned/Scaffold Route Groups

These endpoints currently return `501 Not Implemented`:

- `/api/v1/taxonomy*`
- `/api/v1/analytics*`
- `/api/v1/search`
- `/api/v1/memory*`
- `/api/v1/sandbox*`

## WebSocket Endpoints

### `WS /api/v1/ws/chat`

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

For command payloads:

```json
{
  "type": "command",
  "command": "load_document",
  "args": {"path": "README.md", "alias": "active"},
  "session_id": "session-123"
}
```

Server envelopes include:

- `{"type":"event","data":...}`
- `{"type":"command_result","command":"...","result":{...}}`
- `{"type":"error","message":"..."}`

### `WS /api/v1/ws/execution`

Execution stream for observability/artifact consumers.

Query params:

- `workspace_id`
- `user_id`
- `session_id` (required)

Event types:

- `execution_started`
- `execution_step`
- `execution_completed`

`execution_step` payload shape is additive and includes:

- `step.id`, `step.parent_id`, `step.type`, `step.label`, `step.timestamp`
- optional `step.depth`
- optional `step.actor_kind` (`root_rlm | sub_agent | delegate | unknown`)
- optional `step.actor_id`
- optional `step.lane_key`

Execution payload sanitization is tunable via environment variables:

- `WS_EXECUTION_MAX_TEXT_CHARS` (default: `65536`)
- `WS_EXECUTION_MAX_COLLECTION_ITEMS` (default: `500`)
- `WS_EXECUTION_MAX_RECURSION_DEPTH` (default: `12`)

## Contract Verification

```bash
# REST contract
rg -n "^  /" openapi.yaml

# WebSocket routes (not in OpenAPI)
rg -n "@router.websocket" src/fleet_rlm/server/routers/ws/api.py
```
