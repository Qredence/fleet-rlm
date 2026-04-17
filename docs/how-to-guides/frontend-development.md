# Frontend Development

This guide documents the current frontend workflow for `fleet-rlm`. For the
latest subsystem conventions, treat
[`src/frontend/AGENTS.md`](../../src/frontend/AGENTS.md) as the source of truth.

## Quick Start

```bash
# from repo root
cd src/frontend
pnpm install --frozen-lockfile
pnpm run dev
```

The dev server runs at `http://localhost:5173` and proxies `/api/v1`,
`/health`, and `/ready` to the backend at `http://localhost:8000`.

## Current Source Layout

Frontend source lives under `src/frontend/src/`.

| Path | Purpose |
| --- | --- |
| `routes/` | Thin TanStack Router wrappers, redirects, auth pages, and not-found handling |
| `features/layout/` | Shell chrome, route sync, sidebar, header, dialogs, canvas host |
| `features/workspace/` | Workbench screen, transcript, inspector, run panel, composer |
| `features/volumes/` | Mounted volume browser and preview flow |
| `features/history/` | Session list, detail drawer, replay surfaces |
| `features/optimization/` | Optimization tabs and forms |
| `features/settings/` | Settings dialog/page and runtime settings forms |
| `lib/workspace/` | Zustand stores, runtime adapters, hydration reducers, transcript shaping |
| `lib/rlm-api/` | REST and websocket clients plus generated API types |
| `stores/` | Shell/navigation state |
| `components/ui/` | Shared shadcn/Base UI primitives |
| `components/ai-elements/` | AI Elements rendering primitives |
| `components/product/` | Reusable product composition built from the shared layers |
| `app/` | App bootstrap and providers |

## Product Surface Rules

- Supported surfaces are `/app/workspace`, `/app/volumes`, `/app/history`,
  `/app/optimization`, and `/app/settings`.
- Retired `taxonomy`, `skills`, `memory`, and `analytics` paths should fall
  through to `/404`.
- Route wrappers must stay thin and should not own page logic.
- New work should target `features/*`, `lib/*`, or `components/product/*`, not
  a resurrected screen layer.

## Runtime And API Contract Rules

- `/api/v1/ws/execution` is the canonical conversational websocket.
- `/api/v1/ws/execution/events` is the passive execution subscription stream.
- The workbench should hydrate from `execution_completed.summary` and
  `final_artifact`.
- `daytona_pilot` is the public runtime label in the UI.
- Runtime controls stay aligned with `execution_mode`, `repo_url`, `repo_ref`,
  `context_paths`, and `batch_concurrency`.

## Shell And Layout Rules

- `RootLayout` owns the shell chrome and responsive split layout.
- `RouteSync` keeps the URL and shell store aligned.
- Volumes opens the canvas automatically.
- Settings, Optimization, and History close the canvas.
- Mobile uses the bottom sheet canvas and bottom tab bar.

## Environment

Expected frontend environment:

- `VITE_FLEET_API_URL=http://localhost:8000`
- `VITE_FLEET_TRACE=true`

Optional overrides:

- `VITE_FLEET_WS_URL`
- `VITE_ENTRA_CLIENT_ID`
- `VITE_ENTRA_SCOPES`
- `VITE_PUBLIC_POSTHOG_API_KEY`
- `VITE_PUBLIC_POSTHOG_HOST`

If `VITE_FLEET_WS_URL` is unset, websocket URLs are derived from
`VITE_FLEET_API_URL`.

## OpenAPI Sync Workflow

The canonical HTTP contract lives at `openapi.yaml` in the repo root.

If backend route, schema, or OpenAPI-facing metadata changes, regenerate the
root spec first:

```bash
# from repo root
uv run python scripts/openapi_tools.py generate
```

Then sync or verify the frontend artifacts:

```bash
# from src/frontend
pnpm run api:sync
pnpm run api:check
```

Generated files:

- `openapi/fleet-rlm.openapi.yaml`
- `src/lib/rlm-api/generated/openapi.ts`

Do not edit generated files manually.

## React And UI Conventions

- React 19 direct ref passing is preferred over introducing `forwardRef` by
  default.
- Keep global theme primitives in `src/styles/globals.css`.
- Prefer the shared shadcn/Base UI baseline over one-off wrappers or parallel
  token systems.
- Keep `@/lib/utils` as the canonical `cn()` import path.
- Preserve the current `features/*` ownership model when adding UI.

## Validation

For the repo-aligned frontend gate:

```bash
# from src/frontend
pnpm install --frozen-lockfile
pnpm run api:check
pnpm run type-check
pnpm run lint:robustness
pnpm run test:unit
pnpm run build
```

For the broader repo gate:

```bash
# from repo root
make check
```
