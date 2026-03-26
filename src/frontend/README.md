# frontend

Frontend for `fleet-rlm`, built with React 19 and the repo-standard `pnpm` + Vite+ workflow.

## Install

```bash
cd src/frontend
pnpm install --frozen-lockfile
```

## Run

```bash
cd src/frontend
pnpm run dev
```

The development server runs on `http://localhost:5173` and proxies `/api/v1` and `/health` to the backend on `http://localhost:8000`.

## Backend Contract

This frontend targets the current FastAPI surface:

- REST: `/health`, `/ready`, `GET /api/v1/auth/me`, `GET /api/v1/sessions/state`, `/api/v1/runtime/*`, `POST /api/v1/traces/feedback`
- WebSocket: `/api/v1/ws/chat`, `/api/v1/ws/execution`

Supported product surfaces:

- `/app/workspace`
- `/app/volumes`
- `/app/settings`

Retired `/app/taxonomy*`, `/app/skills*`, `/app/memory`, and `/app/analytics` routes should fall through to `/404`.

## Environment

Create a local `.env` file from `.env.example`.

Expected values:

- `VITE_FLEET_API_URL=http://localhost:8000`
- `VITE_FLEET_WORKSPACE_ID=default`
- `VITE_FLEET_USER_ID=fleetwebapp-user`
- `VITE_FLEET_TRACE=true`

Optional overrides:

- `VITE_FLEET_WS_URL`
- `VITE_ENTRA_CLIENT_ID`
- `VITE_ENTRA_SCOPES`
- `VITE_PUBLIC_POSTHOG_API_KEY`
- `VITE_PUBLIC_POSTHOG_HOST`

If `VITE_FLEET_WS_URL` is unset, the frontend derives `/api/v1/ws/chat` and `/api/v1/ws/execution` from `VITE_FLEET_API_URL`.

### Backend Startup

From the repo root:

```bash
uv run fleet-rlm serve-api --port 8000
```

## OpenAPI Sync

The frontend keeps a tracked snapshot of the backend OpenAPI spec and generated TypeScript types.

From the repo root, regenerate the canonical spec when backend route, schema, or OpenAPI-facing doc metadata changes:

```bash
uv run python scripts/openapi_tools.py generate
```

From `src/frontend`, sync or verify the frontend artifacts:

```bash
pnpm run api:sync
pnpm run api:check
```

Generated files:

- `openapi/fleet-rlm.openapi.yaml`
- `src/lib/rlm-api/generated/openapi.ts`

Do not edit these files manually.

## Quality Checks

```bash
cd src/frontend
pnpm run api:check
pnpm run type-check
pnpm run lint:robustness
pnpm run test:unit
pnpm run build
```

For the full frontend suite, including Playwright:

```bash
pnpm run check
```
