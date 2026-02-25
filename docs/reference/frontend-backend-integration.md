# Frontend ↔ Backend Integration

This document captures the current integration contract between:

- Frontend SPA: `src/frontend`
- Backend API: `src/fleet_rlm/server`

## API Base and Routing

Backend serves:

- Health: `/health`, `/ready`
- Versioned API: `/api/v1/*`
- WebSockets: `/api/v1/ws/chat`, `/api/v1/ws/execution`

## Backend Surfaces Used by Frontend

Primary interactive/chat surfaces:

- `POST /api/v1/chat`
- `WS /api/v1/ws/chat`
- `WS /api/v1/ws/execution`

Runtime setup surfaces:

- `GET /api/v1/runtime/settings`
- `PATCH /api/v1/runtime/settings` (local-only writes)
- `POST /api/v1/runtime/tests/modal`
- `POST /api/v1/runtime/tests/lm`
- `GET /api/v1/runtime/status`

Compatibility/stub surfaces that may still be present in UI flows:

- Legacy-gated: `/api/v1/tasks*`, `/api/v1/sessions*`
- Planned/stub: `/api/v1/taxonomy*`, `/api/v1/analytics*`, `/api/v1/search`, `/api/v1/memory*`, `/api/v1/sandbox*`

## WebSocket Behavior

### `/api/v1/ws/chat`

- Accepts `message`, `cancel`, and `command` payloads.
- Emits `event`, `command_result`, and `error` envelopes.
- Auth claims are canonical tenant/user authority.

### `/api/v1/ws/execution`

- Dedicated execution stream for artifact/step visualization.
- Filters by subscription identity (`workspace_id`, `user_id`, `session_id`).
- Emits `execution_started`, `execution_step`, `execution_completed`.

## Environment Variables

Frontend connectivity is typically driven by:

- `VITE_FLEET_API_URL`
- `VITE_FLEET_WS_URL`
- `VITE_FLEET_WORKSPACE_ID`
- `VITE_FLEET_USER_ID`
- `VITE_FLEET_TRACE`

## Validation Checklist

From repo root:

```bash
uv run fleet-rlm serve-api --port 8000
rg -n "^  /" openapi.yaml
rg -n "@router.websocket" src/fleet_rlm/server/routers/ws.py
```

From `src/frontend` (optional frontend validation):

```bash
bun install --frozen-lockfile
bun run check
```

## Change Policy

If backend routes or payload shapes change, update this file in the same PR as the code change.
