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

## Core Backend Integration (fleet-rlm)

This repo supports a core backend mode for the skill-creation route (`/`) using:
- REST endpoints from fleet-rlm OpenAPI
- WebSocket streaming via `/ws/chat`

### Environment

Create a local `.env` file from `.env.example`.

Required values for backend mode:
- `VITE_FLEET_API_URL=http://localhost:8000`
- `VITE_FLEET_WS_URL=ws://localhost:8000/ws/chat`
- `VITE_FLEET_WORKSPACE_ID=default`
- `VITE_FLEET_USER_ID=fleetwebapp-user`
- `VITE_FLEET_TRACE=true`
- `VITE_FLEET_ENABLE_LEGACY_API_PROBES=false` (default)

If these are not configured, the app falls back to mock/simulated chat behavior.

Legacy `/api/v1/*` capability probing is disabled by default to avoid noisy 404s
against backends that only expose the core fleet-rlm routes. Set
`VITE_FLEET_ENABLE_LEGACY_API_PROBES=true` to re-enable legacy probe requests.

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
