# Frontend ↔ FastAPI Integration (Current State)

This document describes the current, supported integration model between:
- frontend SPA: `src/frontend`
- backend API: `src/fleet_rlm/server`

## Supported backend surface

The frontend is aligned to the FastAPI routes exposed by the backend OpenAPI/runtime:
- `GET /health`
- `GET /ready`
- `POST /chat`
- `POST /tasks/basic`
- `POST /tasks/architecture`
- `POST /tasks/long-context`
- `POST /tasks/check-secret`
- `GET /sessions/state`
- `WS /ws/chat`

## Frontend API layer ownership

The canonical frontend backend layer is:
- `src/frontend/src/app/lib/rlm-api/*`

Legacy compatibility layer `src/frontend/src/app/lib/api/*` has been removed in FastAPI-only mode.

## Runtime behavior expectations

- Primary interactive chat streaming uses `WS /ws/chat`.
- Non-streaming backend calls use `rlm-api` REST clients.
- Unsupported UI sections must be explicitly gated in UX and routed to capability notices.

## Environment configuration

Frontend backend connectivity is controlled by:
- `VITE_FLEET_API_URL`
- `VITE_FLEET_WS_URL`
- `VITE_FLEET_WORKSPACE_ID`
- `VITE_FLEET_USER_ID`
- `VITE_FLEET_TRACE`

## Validation checklist

From `src/frontend`:

```bash
bun run type-check
bun run lint
bun run build
```

From repo root (backend import sanity):

```bash
uv run python -c "import fleet_rlm.server.main as m; print(bool(m.app))"
```

## Notes

If backend/route behavior changes, update this document in the same PR.
