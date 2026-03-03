# Frontend AGENTS.md

## Purpose
This repository hosts the frontend app for the fleet-rlm ecosystem.

## Tooling
- Runtime/package manager: `bun`
- Install deps: `bun install`
- Dev server: `bun run dev`
- Build: `bun run build`
- Type check: `bun run type-check`
- Lint: `bun run lint`
- Robustness lint gate: `bun run lint:robustness`
- Unit tests: `bun run test:unit`
- E2E smoke tests: `bun run test:e2e`
- Full quality gate: `bun run check`

## Backend Integration
Core backend integration targets `fleet-rlm` at:
`<path-to-your-fleet-rlm-repo>`

### OpenAPI Sync Workflow
- Sync spec snapshot: `bun run api:sync-spec`
- Generate TS types: `bun run api:types`
- Full sync: `bun run api:sync`
- Drift check (must be clean): `bun run api:check`

Generated file policy:
- `src/lib/rlm-api/generated/openapi.ts` is generated and must not be edited manually.

## Frontend API Modules
- Sole backend layer: `src/lib/rlm-api/*`
- Legacy layer `src/lib/api/*` is removed and must not be reintroduced.

### API Layer Ownership
- Use `src/lib/rlm-api/*` for all backend contracts (`/health`, `/ready`, `/api/v1/sessions/state`, `/api/v1/runtime/*`, `/api/v1/ws/chat`, `/api/v1/ws/execution`).
- New frontend data work must map to existing FastAPI endpoints or be gated as unsupported in UI.
- Unsupported sections (`skills`, `taxonomy`, `memory`, `analytics`) stay visible but disabled with a capability notice.

### Runtime Conventions
- Route modules are lazy-loaded through `src/lib/perf/lazyWithRetry.ts` and `src/lib/perf/routePreload.tsx`.
- Navigation preloads likely next routes on intent (`TopHeader`, `mobile-tab-bar`) to reduce first-click latency.
- Router errors must render `RouteErrorPage` (never rely on React Router’s default crash screen).
- Skill creation chat flow should use backend runtime only (no legacy API fallback path).

## Environment Variables
- `VITE_FLEET_API_URL`
- `VITE_FLEET_WS_URL`
- `VITE_FLEET_WORKSPACE_ID`
- `VITE_FLEET_USER_ID`
- `VITE_FLEET_TRACE`

## Validation Expectations
Before finishing backend-integration changes, run:
0. `bun install`
1. `bun run api:sync`
2. `bun run api:check`
3. `bun run type-check`
4. `bun run lint:robustness`
5. `bun run test:unit`
6. `bun run build`
7. `bun run test:e2e`
8. `bun run check`
