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

Generated file policy:
- `src/app/lib/rlm-api/generated/openapi.ts` is generated and must not be edited manually.

## Frontend API Modules
- Legacy broad API layer: `src/app/lib/api/*` (mock-first, existing pages)
- Core fleet-rlm API layer: `src/app/lib/rlm-api/*` (chat + tasks + sessions-state + WS)

### API Layer Ownership
- Use `src/app/lib/rlm-api/*` for core fleet-rlm backend contracts (`/health`, `/ready`, `/chat`, `/tasks/*`, `/sessions/state`, `/ws/chat`).
- Use `src/app/lib/api/*` for legacy mock/fallback surfaces and resiliency wrappers around optional `/api/v1/*` capabilities.
- Do not add new core backend endpoint contracts to `src/app/lib/api/*`; keep them in `src/app/lib/rlm-api/*`.
- These boundaries are lint-enforced via `no-restricted-imports` in `eslint.config.js`.

### Allowed Usage Matrix
| Producer Layer | Allowed Consumers | Forbidden Dependencies | Correct Import Example |
| --- | --- | --- | --- |
| `src/app/lib/rlm-api/*` | `pages/skill-creation/*`, runtime adapters, backend chat integration | `src/app/lib/api/*` | `import { isRlmCoreEnabled } from "../../lib/rlm-api";` |
| `src/app/lib/api/*` | legacy hooks (`useSkills`, `useMemory`, `useTaxonomy`, etc.), fallback adapters | `src/app/lib/rlm-api/*` | `import { getApiCapabilities } from "../../lib/api/capabilities";` |

### Runtime Resilience Conventions
- Route modules are lazy-loaded through `src/app/lib/perf/lazyWithRetry.ts` and `src/app/lib/perf/routePreload.tsx`.
- Navigation preloads likely next routes on intent (`TopHeader`, `mobile-tab-bar`) to reduce first-click latency.
- Router errors must render `RouteErrorPage` (never rely on React Router’s default crash screen).
- Legacy API hooks (`useSkills`, `useMemory`, `useTaxonomy`, `useAnalytics`, `useFilesystem`) must expose:
  - `dataSource: 'api' | 'mock' | 'fallback'`
  - `degradedReason?: string`
- Endpoint support probing lives in `src/app/lib/api/capabilities.ts`. Legacy `/api/v1/*` probing is disabled by default and can be re-enabled with `VITE_FLEET_ENABLE_LEGACY_API_PROBES=true`.
- Unsupported `/api/v1/*` endpoints must degrade to local mock data without fatal UI failure.

## Environment Variables
- `VITE_FLEET_API_URL`
- `VITE_FLEET_WS_URL`
- `VITE_FLEET_ENABLE_LEGACY_API_PROBES`
- `VITE_FLEET_WORKSPACE_ID`
- `VITE_FLEET_USER_ID`
- `VITE_FLEET_TRACE`

## Validation Expectations
Before finishing backend-integration changes, run:
0. `bun install`
1. `bun run api:sync`
2. `bun run type-check`
3. `bun run lint:robustness`
4. `bun run test:unit`
5. `bun run build`
6. `bun run test:e2e`
7. `bun run check`
