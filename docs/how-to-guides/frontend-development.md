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

The development server runs at `http://localhost:5173` and proxies
`/api/v1` and `/health` to the backend at `http://localhost:8000`.

## Repo-Aligned Commands

All commands below run from `src/frontend/`:

| Command | Description |
| --- | --- |
| `pnpm run dev` | Start the Vite+ development server |
| `pnpm run build` | Build the production bundle |
| `pnpm run preview` | Preview the production bundle locally |
| `pnpm run type-check` | Run TypeScript type checks |
| `pnpm run lint` | Run the frontend lint rules |
| `pnpm run lint:robustness` | Run the repo-aligned frontend lint lane |
| `pnpm run format` | Format frontend source files with Vite+ `vp fmt` (backed by Oxc/Oxfmt) |
| `pnpm run format:check` | Check frontend formatting with `vp fmt --check` |
| `pnpm run test:unit` | Run Vitest unit tests |
| `pnpm run test:watch` | Run Vitest in watch mode |
| `pnpm run test:coverage` | Run Vitest with coverage output |
| `pnpm run test:e2e` | Run Playwright end-to-end tests |
| `pnpm run api:sync` | Copy the root OpenAPI spec and regenerate TS types |
| `pnpm run api:check` | Fail if `api:sync` would change tracked generated files |
| `pnpm run check` | Run type-check, lint, unit tests, build, and e2e |

## Current Source Layout

Frontend source lives under `src/frontend/src/`.

| Path | Purpose |
| --- | --- |
| `routes/` | File-based routes, redirects, auth pages, and `/404` handling |
| `screens/` | Top-level product surfaces for workspace, volumes, settings, and shell |
| `app/` | Behavior-heavy shell/workspace internals that should not live in route files |
| `components/ui/` | Shared shadcn/Base UI primitives and thin local extensions |
| `components/ai-elements/` | Transcript and chat-specific rendering primitives |
| `lib/rlm-api/` | REST client, websocket client, config, and generated OpenAPI types |
| `lib/workspace/` | Workspace-specific adapters, normalizers, and helper logic |
| `stores/` | Shared Zustand state |
| `styles/globals.css` | Tailwind v4 baseline tokens and app-wide styles |

## Product Surface Rules

- Supported frontend surfaces are `/app/workspace`, `/app/volumes`, and
  `/app/settings`.
- Retired `/app/taxonomy*`, `/app/skills*`, `/app/memory`, and
  `/app/analytics` routes should fall through to `/404`.
- Thin route wrappers under `routes/` should render screen modules instead of
  owning page logic.
- `workspace`, `volumes`, and `settings` behavior should stay in `screens/`,
  `app/`, and `lib/` rather than reintroducing the older `features/` layout.

## Runtime and API Contract Rules

- `/api/v1/ws/execution` is the canonical workbench stream.
- Frontend workbench state should hydrate from
  `execution_completed.summary`, not from legacy chat-final
  payloads.
- `daytona_pilot` is the public runtime mode and sends `execution_mode`,
  `repo_url`, `repo_ref`, `context_paths`, and `batch_concurrency`.

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

If `VITE_FLEET_WS_URL` is unset, the frontend derives the websocket URL for
`/api/v1/ws/execution` from `VITE_FLEET_API_URL`.

## OpenAPI Sync Workflow

The canonical HTTP contract lives at `openapi.yaml` in the repo root.

If backend route, schema, or OpenAPI-facing doc metadata changes, regenerate the
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

## React and UI Conventions

- React 19 refs should use direct ref passing instead of introducing
  `forwardRef` by default.
- Prefer the shared shadcn/Base UI baseline over one-off wrapper components or
  parallel token systems.
- Keep global theme primitives in `src/styles/globals.css`.
- Keep `@/lib/utils` as the canonical `cn()` import path.

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
make quality-gate
```
