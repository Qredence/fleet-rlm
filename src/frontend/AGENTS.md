# Frontend Agent Instructions

## Scope and Reading Order

This file is written for AI coding agents modifying the frontend app in `src/frontend/`.
Read the root [AGENTS.md](../../AGENTS.md) first for shared repo rules.
Consult [src/fleet_rlm/AGENTS.md](../fleet_rlm/AGENTS.md) when frontend work changes backend routes, websocket payloads, runtime modes, auth behavior, or OpenAPI-facing schemas.

## Frontend Quickstart

Before editing frontend code:

- Read `src/frontend/package.json` for canonical scripts.
- Inspect the owning route, feature, component, and lib modules before adding files.
- Preserve the supported app surfaces: `workspace`, `volumes`, `optimization`, and `settings`.
- Keep route wrappers thin; put feature behavior in the owning module tree.
- Do not hand-edit generated files.

Frontend source-of-truth files:

- `src/frontend/package.json` for scripts and validation
- `src/frontend/vite.config.ts` for lint/test/build configuration and import-boundary rules
- `src/frontend/src/routes/*` for supported surfaces and not-found behavior
- `src/frontend/src/features/layout/*` for canonical app-chrome public entrypoints
- `src/frontend/src/components/ui/*` for canonical shadcn/Base UI source components
- `src/frontend/src/components/ai-elements/*` for canonical AI Elements source components
- `src/frontend/src/components/patterns/*` for app-owned reusable composition built from registry layers
- `src/frontend/src/lib/rlm-api/*` for REST and websocket integration
- `src/frontend/src/styles/globals.css` for the Tailwind v4 theme baseline and tokens
- `src/frontend/components.json` for the shadcn registry/style baseline
- `openapi.yaml` and `src/frontend/src/lib/rlm-api/generated/openapi.ts` for API contract alignment

Generated or synced artifacts to avoid hand-editing:

- `src/routeTree.gen.ts`
- `src/lib/rlm-api/generated/openapi.ts`
- `openapi/fleet-rlm.openapi.yaml`
- `dist/`

## Agent Priorities

- Preserve the supported app surfaces: `Workbench`, `Volumes`, `Optimization`, and `Settings`.
- Keep retired `taxonomy`, `skills`, `memory`, and `analytics` paths falling through to `/404`.
- Keep runtime labels, websocket behavior, and request controls aligned with the backend contract.
- Treat `/api/v1/ws/chat` as transcript-first and `/api/v1/ws/execution` as the canonical execution/workbench stream.
- Hydrate workbench state from `execution_completed.summary`, not Daytona-only chat-final payload scraping.
- Render Daytona `sandbox_output` status frames as sandbox/debug trace cards while keeping `trajectory_step` and `reasoning_step` as the main live trace surfaces.
- Prefer the shadcn/Base UI baseline over one-off wrappers, parallel token layers, or custom mini-design-systems.
- Keep the registry-owned layers recognizable and diff-friendly: `src/components/ui/*` and `src/components/ai-elements/*` stay canonical.

## Tooling and Framework

- Package manager: `pnpm` with `pnpm install --frozen-lockfile`
- Build/lint/format runtime: Vite+ (`vp`) via `pnpm run ...`
- Framework stack: React 19 + TanStack Router + TanStack Query + Zustand

Canonical commands:

- `pnpm install --frozen-lockfile`
- `pnpm run dev`
- `pnpm run build`
- `pnpm run type-check`
- `pnpm run lint`
- `pnpm run lint:robustness`
- `pnpm run format`
- `pnpm run test:unit`
- `pnpm run test:watch`
- `pnpm run test:coverage`
- `pnpm run test:e2e`
- `pnpm run api:sync`
- `pnpm run api:check`
- `pnpm run check`

Targeted execution:

- Unit test: `pnpm run test:unit src/path/to/file.test.ts`
- E2E test: `pnpm run test:e2e tests/e2e/file.spec.ts`

## Frontend Layers

Registry-aligned component layers:

- `src/components/ui/*` for shadcn/Base UI primitives and thin local extensions
- `src/components/ai-elements/*` for AI Elements registry components
- `src/components/patterns/*` for app-owned reusable compositions built from `ui` and `ai-elements`
- `src/features/layout/*` for canonical app-chrome entrypoints that compose the current shell implementation
- `src/components/` root for a very small set of global compatibility exports such as `brand-mark.tsx`

Rules for those layers:

- Keep `components/ui` thin, semantic, and free of feature/runtime imports
- Keep `components/ai-elements` composable and registry-aligned; do not collapse them into feature-specific monoliths
- Use `components/patterns` for reusable product composition such as empty states, route skeletons, panel shells, and form/panel structures
- Route app-chrome consumers through `features/layout/*` before reaching transitional `app/shell/*` or `screens/shell/*` modules
- New shared layout/app-chrome naming should prefer `layout` over `shell` when creating new architecture surfaces

## Frontend Map

Routing ownership:

- `src/router.tsx` owns the router instance
- `src/routeTree.gen.ts` is generated and should not be edited
- File-based routes under `src/routes/` define product surfaces and catchall/not-found behavior
- Route files should compose feature or screen entry modules instead of embedding feature logic

Current surface ownership:

- `src/screens/workspace/` is the current top-level workspace surface and public screen contract
- `src/screens/volumes/` owns the volume browser entrypoints and public screen contracts
- `src/screens/settings/` owns settings entrypoints
- `src/features/layout/` owns canonical app-chrome public entrypoints and compatibility exports
- `src/screens/shell/` owns the current app-frame implementation beneath `features/layout/`; new architectural naming should prefer `layout`
- `src/app/workspace/` owns workspace UI internals such as transcript, composer, inspector, workbench, and queue helpers
- `src/app/shell/` owns current app-frame composed surfaces such as command palette and route sync; treat it as a transitional implementation layer behind `features/layout/`
- `src/lib/workspace/` owns backend event adapters, run-workbench adapters, chat stores, and normalized runtime/frame shaping
- `src/lib/rlm-api/` owns REST and websocket clients plus generated API types
- `src/stores/` owns cross-app shell/layout and navigation state

Important boundaries to preserve:

- Keep `src/screens/*` thin; move reusable feature logic into `src/app/*`, `src/lib/*`, or `src/components/patterns/*`
- Keep external layout/app-chrome imports pointed at `src/features/layout/*`; only reach into `src/screens/shell/*` or `src/app/shell/*` while refactoring that layer itself
- `src/screens/workspace/use-workspace.ts` is the public workspace contract; implementation-heavy helpers belong under `src/lib/workspace/`
- `src/screens/workspace/workspace-canvas-panel.tsx` stays the shell-facing canvas surface; canvas internals belong under `src/app/workspace/`
- Assistant transcript/content modeling belongs under `src/app/workspace/assistant-content/model/`
- Do not recreate a screen-layer `workspace-adapter.ts`; workspace adapter logic belongs in `src/lib/workspace/`
- The Volumes provider switcher is page-scoped and must not become a global runtime setting

Import-boundary rules enforced in `src/frontend/vite.config.ts`:

- `src/components/ui/*`, `src/components/ai-elements/*`, and `src/components/patterns/*` must not import from `src/screens/*`
- Workspace runtime/state modules in `src/lib/workspace/*` must not depend on workspace UI modules
- `src/screens/shell/*` must consume workspace and volumes through top-level screen contracts only
- Keep `@/lib/utils` as the canonical `cn()` import path

## UI and Runtime Rules

Design and styling rules:

- Theme primitives live in `src/styles/globals.css`
- Keep the Tailwind v4 baseline canonical: `tailwindcss`, `tw-animate-css`, and `@theme inline`
- Use semantic tokens and shared variants instead of arbitrary colors or local token layers
- Keep typography, spacing, and layering aligned with the shared shadcn/Base UI baseline
- Preserve the shell/layout root stacking context so portaled overlays layer correctly
- Shared visual recipes should become `components/patterns/*`, not duplicated feature-local wrappers

React/runtime rules:

- Prefer React 19 direct ref passing over introducing `forwardRef` by default
- `modal_chat` is the default runtime path and sends `execution_mode`
- `daytona_pilot` sends `repo_url`, `repo_ref`, `context_paths`, and `batch_concurrency`
- Runtime labels shown to users are `"Modal chat"` and `"Daytona pilot"`
- Shared runtime status queries belong in `src/hooks/use-runtime-status.ts`
- The Volumes surface represents mounted durable storage, not the transient live workspace

Naming and file-layout rules:

- Prefer `kebab-case` for new handwritten feature files, while preserving existing local conventions and required framework exceptions such as `App.tsx`, `__root.tsx`, and `$.tsx`
- Keep React component symbols in `PascalCase` and hooks in `useThing` form
- Keep tests colocated with the owning module under `__tests__/` when practical
- Tests for `src/lib/workspace/*` and `src/app/workspace/*` should import those owners directly, not via screen compatibility barrels
- For new architecture naming, prefer `layout` for app chrome and structural composition instead of `shell`

## Environment and Contract Sync

Expected frontend environment:

- `VITE_FLEET_API_URL=http://localhost:8000`
- `VITE_FLEET_TRACE=true`

Optional frontend environment:

- `VITE_FLEET_WS_URL`
- `VITE_AGENTATION_ENDPOINT`
- `VITE_ENTRA_CLIENT_ID`
- `VITE_ENTRA_SCOPES`
- `VITE_PUBLIC_POSTHOG_API_KEY`
- `VITE_PUBLIC_POSTHOG_HOST`

Backend startup for frontend work:

- `uv run fleet-rlm serve-api --port 8000`

OpenAPI sync workflow:

- If backend route/schema metadata changed, regenerate the root spec first with `uv run python scripts/openapi_tools.py generate`
- `pnpm run api:sync` copies the root spec and regenerates frontend types
- `pnpm run api:check` reruns sync and fails if the synced snapshot or generated types drift
- Keep sync artifacts in the same change instead of hand-editing generated output

## Validation by Change Type

Fast frontend confidence:

- `pnpm install --frozen-lockfile`
- `pnpm run api:check`
- `pnpm run format`
- `pnpm run type-check`
- `pnpm run lint:robustness`
- `pnpm run test:unit`
- `pnpm run build`

Full frontend validation:

- `pnpm run check`

Use the backend or root `AGENTS.md` lane when frontend work changes shared API or websocket contracts.

## Agent Notes

- `components.json` defines the `@/*` alias and the shadcn/Base UI style baseline.
- The dev server proxies `/api/v1`, `/health`, and `/ready` to `localhost:8000`.
- PostHog initializes in `src/main.tsx` when `VITE_PUBLIC_POSTHOG_API_KEY` is set.
- Keep runtime labels, route behavior, and endpoint expectations aligned with the backend contract.
