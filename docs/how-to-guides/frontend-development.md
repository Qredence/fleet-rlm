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
| `features/layout/` | Shell chrome, route sync, sidebar, header, and dialogs |
| `features/workspace/` | Workbench chat, transcript, session controls, and workspace sidepanel |
| `features/volumes/` | Full-page mounted volume browser and preview flow |
| `features/settings/` | Settings dialog/page and runtime settings forms |
| `lib/workspace/` | Zustand stores, runtime adapters, hydration reducers, transcript shaping |
| `lib/rlm-api/` | REST and websocket clients plus generated API types |
| `stores/` | Shell/navigation state |
| `components/ui/` | Shared shadcn/Base UI primitives |
| `components/ai-elements/` | AI Elements rendering primitives |
| `components/product/` | Reusable product composition built from the shared layers |
| `app/` | App bootstrap and providers |

## Product Surface Rules

- Supported surfaces are `/app/workspace`, `/app/volumes`, and `/app/settings`.
- Retired `taxonomy`, `skills`, `memory`, `analytics`, `history`, and
  `optimization` paths should fall through to `/404`.
- Route wrappers must stay thin and should not own page logic.
- New work should target `features/*`, `lib/*`, or `components/product/*`, not
  a resurrected screen layer.
- Workbench chat is primary. Its sidepanel is workspace-local,
  collapsible/resizable, and limited to `Trajectories`, `Graph`, and `Volume`
  tabs.
- The workspace `Volume` tab uses Daytona volume APIs with inline preview;
  `/app/volumes` remains the full-page durable volume browser.

## Runtime And API Contract Rules

- `/api/v1/ws/execution` is the canonical conversational websocket.
- `/api/v1/ws/execution/events` is the passive execution subscription stream.
- The workbench should hydrate from `execution_completed.summary` and
  `final_artifact`.
- The UI treats the workbench runtime as Daytona-backed; request-side provider labels are not part of the public contract.
- Runtime controls stay aligned with `execution_mode`, `repo_url`, `repo_ref`,
  `context_paths`, and `batch_concurrency`.

## Shell And Layout Rules

- `RootLayout` owns shell chrome; workspace owns sidepanel layout.
- `RouteSync` keeps the URL and shell store aligned.
- Mobile uses the bottom tab bar; responsive sidepanel behavior stays inside
  `features/workspace/*`.

## Environment

Expected frontend environment:

- `VITE_FLEET_API_URL=http://localhost:8000`
- `VITE_FLEET_TRACE=true`

Optional overrides:

- `VITE_FLEET_WS_URL`
- `VITE_NEON_AUTH_URL`
- `VITE_NEON_AUTH_SOCIAL_PROVIDERS`
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
