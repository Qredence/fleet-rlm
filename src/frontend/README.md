# frontend

Frontend for qredence Fleet, built with React + Vite.

## Install

```bash
bun install
```

## Run

```bash
bun run dev
```

## FastAPI Backend Integration (fleet-rlm)

This frontend is FastAPI-only and targets the current backend surface:
- REST: `/health`, `/ready`, `/api/v1/chat`, `/api/v1/tasks/*`, `/api/v1/sessions/state`
- WebSocket: `/api/v1/ws/chat`, `/api/v1/ws/execution`

Unsupported sections remain visible in navigation but are intentionally disabled:
- `skills`
- `taxonomy`
- `memory`
- `analytics`

### Environment

Create a local `.env` file from `.env.example`.

Required values:
- `VITE_FLEET_API_URL=http://localhost:8000`
- `VITE_FLEET_WS_URL=ws://localhost:8000/ws/chat`
- `VITE_FLEET_WORKSPACE_ID=default`
- `VITE_FLEET_USER_ID=fleetwebapp-user`
- `VITE_FLEET_TRACE=true`

If backend values are missing, the app shows backend-capability notices instead of using legacy/mock API surfaces.

### Backend Startup

From the fleet-rlm repo:

```bash
uv run fleet-rlm serve-api --port 8000
```

## OpenAPI Type Sync

The frontend keeps a local snapshot of backend OpenAPI and generates TS types.

```bash
bun run api:sync-spec
bun run api:types
bun run api:sync
```

`api:sync-spec` defaults to the canonical root spec at `../../openapi.yaml` unless `OPENAPI_SPEC_PATH` is set.

Generated file:
- `src/app/lib/rlm-api/generated/openapi.ts` (do not edit manually)

## Quality Checks

```bash
bun run type-check
bun run lint
bun run test:unit
bun run build
bun run test:e2e
bun run check
```
