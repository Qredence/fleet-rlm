# Frontend Agent Instructions

> For AI coding agents working in `src/frontend/`.
> Read the root [AGENTS.md](../../AGENTS.md) first for shared repo rules.
> Consult [`src/fleet_rlm/AGENTS.md`](../fleet_rlm/AGENTS.md) when changes affect backend routes, websockets, auth, or OpenAPI schemas.

---

## Quickstart Checklist

Before editing:

1. Read `package.json` for canonical scripts.
2. Inspect the owning route, feature, component, or lib module.
3. Do not hand-edit generated files (see list below).
4. Preserve supported surfaces: **Workbench**, **Volumes**, **Optimization**, **Settings**, **History**.
5. Keep retired paths (`taxonomy`, `skills`, `memory`, `analytics`) falling through to `/404`.

---

## Source-of-Truth Files

| Concern                 | File(s)                                                            |
| ----------------------- | ------------------------------------------------------------------ |
| Scripts & validation    | `package.json`                                                     |
| Lint/build/import rules | `vite.config.ts`                                                   |
| Routes & surfaces       | `src/routes/*`                                                     |
| App chrome / layout     | `src/features/layout/*`                                            |
| Product surfaces        | `src/features/{workspace,volumes,settings,optimization,history}/*` |
| UI primitives           | `src/components/ui/*` (shadcn/Base UI)                             |
| AI Elements             | `src/components/ai-elements/*`                                     |
| Product compositions    | `src/components/product/*`                                         |
| API clients & types     | `src/lib/rlm-api/*`                                                |
| Workspace adapters      | `src/lib/workspace/*`                                              |
| Theme / tokens          | `src/styles/globals.css`                                           |
| shadcn config           | `components.json`                                                  |
| API contract            | `openapi.yaml`, `src/lib/rlm-api/generated/openapi.ts`             |

### Generated / Synced — Do Not Hand-Edit

- `src/routeTree.gen.ts`
- `src/lib/rlm-api/generated/openapi.ts`
- `openapi/fleet-rlm.openapi.yaml`
- `dist/`

---

## Architecture

### Component Layers (outer → inner)

1. **`src/components/ui/*`** — shadcn/Base UI primitives. Thin, semantic, no feature/runtime imports.
2. **`src/components/ai-elements/*`** — AI Elements registry. Composable, registry-aligned.
3. **`src/components/product/*`** — Reusable product compositions (empty states, skeletons, panels).
4. **`src/features/layout/*`** — App chrome. Consumes workspace/volumes through feature entrypoints only.
5. **`src/features/{workspace,volumes,settings,optimization,history}/*`** — Canonical surface ownership.
6. **`src/lib/{rlm-api,workspace}/*`** — API clients, adapters, stores, frame shaping.
7. **`src/stores/*`** — Cross-app shell/layout and navigation state.

### Import Boundaries (enforced in `vite.config.ts`)

- `src/components/{ui,ai-elements,product}/*` **must not** import from `src/screens/*`.
- `src/lib/workspace/*` **must not** depend on workspace UI modules.
- `src/features/layout/*` **must** consume workspace/volumes through their feature entrypoints or explicit public contracts.
- `@/lib/utils` is the canonical `cn()` import path.

### Route Ownership

- `src/router.tsx` owns the router instance.
- `src/routes/` defines file-based routes. Keep route wrappers thin; compose feature entry modules (e.g., `screen/*`).
- `src/routeTree.gen.ts` is generated.

### Workspace Structure

Responsibility folders under `src/features/workspace/`:

- `screen/` — route entry
- `conversation/` — chat rendering
- `composer/` — input / prompt UI
- `inspection/` — detail panels
- `workbench/` — execution trace / workbench
- `session/` — session management

Assistant transcript/content modeling belongs under:
`src/features/workspace/conversation/assistant-content/model/`

**Do not** create feature-local `ui/` folders; `src/components/ui/*` is the only primitive `ui` namespace.

---

## Tech Stack

- **Package manager:** `pnpm` (always `pnpm install --frozen-lockfile`)
- **Build / lint / format:** Vite+ (`vp`) via `pnpm run ...`
- **Framework:** React 19 + TypeScript 5.9+
- **Router:** TanStack Router (file-based)
- **State:** Zustand + TanStack Query
- **Styling:** Tailwind CSS v4 + `tw-animate-css` + `@theme inline`
- **Testing:** Vitest (unit), Playwright (e2e)

---

## Canonical Commands

```bash
# Install
pnpm install --frozen-lockfile

# Dev server (proxies /api/v1, /health, /ready → localhost:8000)
pnpm run dev

# Production build
pnpm run build

# Quality
pnpm run type-check
pnpm run lint                # vp lint
pnpm run lint:robustness     # alias
pnpm run format              # vp fmt
pnpm run format:check        # vp fmt --check

# Tests
pnpm run test:unit
pnpm run test:watch
pnpm run test:coverage
pnpm run test:e2e

# API contract sync
pnpm run api:sync            # copy spec + regenerate types
pnpm run api:check           # fail if drift

# Full validation
pnpm run check               # type-check + lint + test:unit + build + test:e2e
```

### Targeted Execution

```bash
pnpm run test:unit src/path/to/file.test.ts
pnpm run test:e2e tests/e2e/file.spec.ts
```

---

## Validation by Change Type

### Fast confidence

```bash
pnpm install --frozen-lockfile
pnpm run api:check
pnpm run format
pnpm run type-check
pnpm run lint:robustness
pnpm run test:unit
pnpm run build
```

### Full confidence

```bash
pnpm run check
```

> When frontend work changes shared API or websocket contracts, also run the backend validation lane from the root `AGENTS.md`.

---

## Design & Styling Rules

- Theme primitives live in `src/styles/globals.css`. Keep the Tailwind v4 baseline canonical.
- Use **semantic tokens and shared variants** — avoid arbitrary colors or local token layers.
- Preserve shell/layout root stacking context for portaled overlays.
- Shared visual recipes belong in `src/components/product/*`, not duplicated locally.

## React & Runtime Rules

- Prefer **React 19 direct ref passing** over `forwardRef` by default.
- `daytona_pilot` is the public runtime label. Request controls: `execution_mode`, `repo_url`, `repo_ref`, `context_paths`, `batch_concurrency`.
- Runtime labels shown to users should describe the Daytona-backed workbench path only.
- Shared runtime status queries: `src/hooks/use-runtime-status.ts`.
- The **Volumes** surface represents mounted durable storage, not the transient live workspace.

## Naming Conventions

- New handwritten feature files: `kebab-case`
- React components: `PascalCase`
- Hooks: `useThing`
- Framework exceptions preserved: `App.tsx`, `__root.tsx`, `$.tsx`

## Testing Conventions

- Colocate tests under `__tests__/` when practical.
- Tests for `src/lib/workspace/*` and `src/features/workspace/{conversation,composer,inspection,screen,session,workbench}/*` should import owners directly, not via route wrappers or compatibility barrels.

---

## Environment Variables

### Expected

```env
VITE_FLEET_API_URL=http://localhost:8000
VITE_FLEET_TRACE=true
```

### Optional

```env
VITE_FLEET_WS_URL
VITE_AGENTATION_ENDPOINT
VITE_ENTRA_CLIENT_ID
VITE_ENTRA_SCOPES
VITE_PUBLIC_POSTHOG_API_KEY
VITE_PUBLIC_POSTHOG_HOST
```

### Backend for frontend dev

```bash
uv run fleet-rlm serve-api --port 8000
```

> The dev server proxies `/api/v1`, `/health`, and `/ready` to `localhost:8000`.
> PostHog initializes in `src/main.tsx` when `VITE_PUBLIC_POSTHOG_API_KEY` is set.

---

## OpenAPI Sync Workflow

If backend route/schema metadata changed:

1. Regenerate root spec: `uv run python scripts/openapi_tools.py generate`
2. Sync frontend artifacts: `pnpm run api:sync`
3. Verify no drift: `pnpm run api:check`

Keep sync artifacts in the same change; never hand-edit generated output.

---

## Agent Notes

- `components.json` defines the `@/*` alias and the shadcn/Base UI style baseline.
- Keep runtime labels, route behavior, and endpoint expectations aligned with the backend contract.
- `src/screens/*` no longer exists. All feature logic lives in `src/features/*`, `src/lib/*`, or `src/components/product/*`.
- `History` is a supported surface at `/app/history`.
- Do not recreate a screen-layer `workspace-adapter.ts`; adapter logic belongs in `src/lib/workspace/`.
- The Volumes provider switcher is **page-scoped** and must not become a global runtime setting.
- Settings should consume the shared optimization form from `features/optimization/optimization-form`.
