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

## Canonical Source Layout
- Route entrypoints and shells live in `src/app/*`.
- Reusable domain UI belongs in `src/features/*`.
- Shared presentational primitives live in `src/components/*`.
- Backend/data adapters live in `src/lib/*`.
- Cross-cutting React hooks live in `src/hooks/*`; Zustand stores live in `src/stores/*`.
- `src/screens/*` is not a general-purpose home for new work. The remaining chat files there are transitional and should only be touched when continuing that refactor.
- Remove empty folders and dead placeholder modules instead of leaving parallel directory schemes behind.

### API Layer Ownership
- Use `src/lib/rlm-api/*` for all backend contracts (`/health`, `/ready`, `/api/v1/sessions/state`, `/api/v1/runtime/*`, `/api/v1/ws/chat`, `/api/v1/ws/execution`).
- New frontend data work must map to existing FastAPI endpoints or be gated as unsupported in UI.
- Supported product surfaces are `workspace`, `volumes`, and `settings`.
- Legacy `taxonomy`, `skills`, `memory`, and `analytics` URLs should redirect to canonical supported routes instead of remaining in primary navigation.

### State Ownership
- Use TanStack Query for backend-backed server state.
- Use Zustand only for ephemeral client state (streaming/chat/session UI state, artifact canvas state, or explicitly demo/mock-only state).
- Do not add new feature state to generic app-wide providers when it can live inside a domain module.

### Mock Data
- Canonical mock data lives under `src/lib/data/mock/*`.
- Import mock data directly from those modules; do not reintroduce compatibility barrels like `src/lib/data/mock-skills.ts`.
- If a screen is mock-only, label it clearly in code and UI instead of silently degrading from a removed backend route.

### Runtime Conventions
- Route modules are lazy-loaded through `src/lib/perf/lazyWithRetry.ts` and `src/lib/perf/routePreload.tsx`.
- Navigation preloads likely next routes on intent (`TopHeader`, `mobile-tab-bar`) to reduce first-click latency.
- Router errors must render `RouteErrorPage` (never rely on React Router’s default crash screen).
- The RLM Workspace chat flow should use backend runtime only (no legacy API fallback path).
- Canonical domain ownership now lives in `src/features/rlm-workspace/*` for chat/runtime UX and `src/features/volumes/*` for the Modal Volume browser; route pages under `src/app/pages/*` should stay thin.
- Product-facing labels should use `RLM Workspace` and `Volumes`; do not introduce new `skill-creation` or `taxonomy` product terminology.
- Keep `src/components/ui/*` limited to primitives and thin wrappers. Shell navigation or workspace composition widgets belong under `src/features/*` or another shared domain folder.

## Environment Variables
- `VITE_FLEET_API_URL`
- `VITE_FLEET_WS_URL`
- `VITE_FLEET_WORKSPACE_ID`
- `VITE_FLEET_USER_ID`
- `VITE_FLEET_TRACE`
- `VITE_ENTRA_CLIENT_ID`
- `VITE_ENTRA_AUTHORITY`
- `VITE_ENTRA_SCOPES`
- `VITE_ENTRA_REDIRECT_PATH`
- Default Entra authority is `https://login.microsoftonline.com/organizations`; use `VITE_ENTRA_AUTHORITY` only when you intentionally need an override.

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
