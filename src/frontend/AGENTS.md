# Frontend Agent Instructions

## Scope and Reading Order

This file is written for AI coding agents modifying the frontend app in `src/frontend/`.
Read the root [AGENTS.md](../../AGENTS.md) first for shared repo rules.
Consult [src/fleet_rlm/AGENTS.md](../fleet_rlm/AGENTS.md) when your frontend change touches backend routes, websocket payloads, runtime modes, or auth behavior.

Frontend source-of-truth files:

- `src/frontend/package.json` for scripts and validation
- `src/frontend/src/routes/*` for supported app surfaces and not-found behavior
- `src/frontend/src/lib/rlm-api/*` for REST and websocket integration
- `src/frontend/src/styles/globals.css` for the Tailwind v4 theme baseline and global tokens
- `src/frontend/components.json` for shadcn registry/style/base configuration
- `openapi.yaml` and `src/frontend/src/lib/rlm-api/generated/openapi.ts` for API contract alignment

## Agent Priorities

- Preserve the supported app surfaces: `Workbench`, `Volumes`, and `Settings`.
- Keep the supported app surface limited to `workspace`, `volumes`, and `settings`; retired `taxonomy`, `skills`, `memory`, and `analytics` paths should continue to fall through to `/404`.
- Do not hand-edit generated files like `src/routeTree.gen.ts` or `src/lib/rlm-api/generated/openapi.ts`.
- Keep runtime labels, websocket behavior, and request controls aligned with the backend contract.
- Treat `/api/v1/ws/chat` as transcript-first and `/api/v1/ws/execution` as the canonical canvas/workbench stream. Frontend workbench state should hydrate from `execution_completed.summary`, not Daytona-only chat-final payloads.
- Daytona `sandbox_output` status frames should render as sandbox/debug trace cards in the transcript, while `trajectory_step` and `reasoning_step` remain the primary live trace surfaces.
- Prefer the shadcn/Base UI baseline over introducing one-off wrapper components or parallel token layers.

## Tooling and Framework

- Package manager: `pnpm` with `pnpm install --frozen-lockfile`
- Build toolchain: Vite+ (`vp`) surfaced through `pnpm run ...`
- Framework stack: React 19 + TanStack Router + TanStack Query + Zustand

Canonical commands:

- `pnpm install --frozen-lockfile`
- `pnpm run dev`
- `pnpm run build`
- `pnpm run type-check`
- `pnpm run lint`
- `pnpm run lint:robustness`
- `pnpm run test:unit`
- `pnpm run test:watch`
- `pnpm run test:coverage`
- `pnpm run test:e2e`
- `pnpm run api:sync`
- `pnpm run api:check`
- `pnpm run check`

Single-test execution:

- Unit: `pnpm run test:unit src/path/to/__tests__/file.test.ts`
- E2E: `pnpm run test:e2e tests/e2e/file.spec.ts`

## Frontend Map

Routing:

- `src/router.tsx` owns the router instance
- `src/routeTree.gen.ts` is generated and should not be edited
- file-based routes under `src/routes/` define app surfaces and catchall/not-found behavior

State management:

- TanStack Query for backend-backed state
- Zustand for ephemeral client state, with cross-app shell state in `src/stores/`
- `src/screens/workspace/use-workspace.ts` is the public workspace state/runtime contract; the implementation-heavy stores, types, and runtime helpers live under `src/lib/workspace/`
- `src/screens/workspace/workspace-canvas-panel.tsx` stays the shell-facing canvas surface; canvas internals live under `src/app/workspace/`
- Volumes file-selection state lives in `src/screens/volumes/use-volumes.ts`
- The Volumes page has a local provider switcher for `modal` vs `daytona`; keep that selector page-scoped and do not route it through global runtime settings.
- `src/stores/navigation-types.ts` owns the shared `NavItem` type used by shell navigation and route helpers

Backend integration:

- `src/lib/rlm-api/client.ts` for REST calls
- `src/lib/rlm-api/wsClient.ts` and related websocket helpers for streaming
- `src/lib/rlm-api/generated/openapi.ts` for generated API types

Feature layout:

- `src/screens/workspace/` is the dominant product slice for chat, runtime, inspector, workbench, and workspace artifacts
- `src/screens/shell/` owns the route shell and shell chrome; shell-private helpers now live under `src/app/shell/` and route-only auth/error pages live under `src/routes/`
- `src/screens/volumes/` owns the volume browser, file preview, and file-selection helpers
- `src/screens/settings/` owns runtime and app settings
- Thin route wrappers under `src/routes/` should render screen modules rather than page-layer wrappers
- Shell code should consume workspace/volumes through top-level screen contracts like `workspace-shell-contract.ts`, `workspace-canvas-panel.tsx`, `volumes-shell-contract.ts`, and `volumes-canvas-panel.tsx` instead of reaching into deep workspace/volumes subdirectories
- `src/screens/workspace/`, `src/screens/volumes/`, `src/screens/settings/`, and `src/screens/shell/` should keep only the top-level screen entry files plus `__tests__/`; move shell-private helpers to `src/app/shell/`, workspace UI internals to `src/app/workspace/`, and workspace adapter logic to `src/lib/workspace/`

Component layout:

- `src/components/ui/` for shared shadcn/Base UI primitives and thin local extensions
- `src/components/ai-elements/` for transcript/message/source/task/tool rendering
- `src/components/` for a very small set of handwritten global components such as `brand-mark.tsx`, `error-boundary.tsx`, and `page-skeleton.tsx`
- `src/app/workspace/` for behavior-bearing workspace UI internals (transcript/composer/inspector/workbench and related helpers)
- `src/lib/workspace/` for workspace-only adapter and frame-normalization helpers
- Prefer shadcn field composition (`FieldGroup`, `Field`, `FieldContent`, `FieldLabel`, `FieldDescription`, `FieldTitle`, `Switch`, `ToggleGroup`) over bespoke settings row wrappers when building forms
- Prefer behavior-bearing screen subcomponents over micro-wrappers; tiny layout-only wrappers should usually be inlined back into the screen component

## UI and Runtime Rules

Design/token rules:

- Theme primitives live in `src/styles/globals.css`
- Keep the Tailwind v4 baseline canonical: `tailwindcss`, `tw-animate-css`, `@theme inline`, and only small app-specific adjustments
- Use semantic tokens and shared shadcn variants instead of arbitrary color values or local mini-design-systems
- Keep typography, icon sizing, spacing, and layering aligned with the shared shadcn/Base UI baseline
- The shell root should preserve an isolated stacking context so portaled overlays layer correctly

React/runtime rules:

- React 19 refs should use direct ref passing instead of introducing `forwardRef` by default
- `modal_chat` is the default runtime path and sends `execution_mode`
- `daytona_pilot` is the experimental workbench path and sends `repo_url`, `repo_ref`, `context_paths`, and `batch_concurrency`
- Runtime labels shown to users are `"Modal chat"` and `"Daytona pilot"`
- Shared runtime status queries belong in `src/hooks/useRuntimeStatus.ts`; settings should compose that hook rather than own the query contract

## Environment and Contract Sync

Expected frontend environment:

- `VITE_FLEET_API_URL=http://localhost:8000`
- `VITE_FLEET_WORKSPACE_ID=default`
- `VITE_FLEET_USER_ID=fleetwebapp-user`
- `VITE_FLEET_TRACE=true`

Optional frontend environment:

- `VITE_FLEET_WS_URL` (optional explicit override; when unset, websocket URLs are derived from `VITE_FLEET_API_URL`)
- `VITE_ENTRA_CLIENT_ID`
- `VITE_ENTRA_SCOPES`
- `VITE_PUBLIC_POSTHOG_API_KEY`
- `VITE_PUBLIC_POSTHOG_HOST`

Backend startup for frontend work:

- `uv run fleet-rlm serve-api --port 8000`

OpenAPI sync:

- If backend route/schema contract metadata changed, regenerate the root spec first with `uv run python scripts/openapi_tools.py generate`
- `pnpm run api:sync` copies the root spec and regenerates frontend types
- `pnpm run api:check` reruns sync and fails only if that sync changes the frontend OpenAPI snapshot or generated types
- If `api:check` produces formatting-only diffs in `openapi/fleet-rlm.openapi.yaml` or `src/lib/rlm-api/generated/openapi.ts`, keep those sync artifacts in the same change rather than hand-editing generated output

Lint and boundary enforcement:

- Frontend lint rules are configured in `src/frontend/vite.config.ts` via Vite+ (`vp`) overrides, not a standalone ESLint config
- `src/components/ui/*` and `src/components/ai-elements/*` must not import from `src/screens/*`
- `src/screens/shell/*` must import workspace and volumes behavior through top-level screen contracts only
- Keep `@/lib/utils` as the canonical `cn()` import path; do not recreate `@/lib/utils/cn`

## Validation by Change Type

Fast frontend confidence:

- `pnpm install --frozen-lockfile`
- `pnpm run api:check`
- `pnpm run type-check`
- `pnpm run lint:robustness`
- `pnpm run test:unit`
- `pnpm run build`

Full frontend validation:

- `pnpm run check`

Use the backend AGENTS file or the root AGENTS file when you need wider validation for shared API or websocket contract changes.

## Agent Notes

- `components.json` defines the `@/*` alias, shadcn registry configuration, and the Base UI-backed style baseline (`base=base`).
- The dev server proxies `/api/v1` and `/health` to `localhost:8000`.
- PostHog initializes in `main.tsx` when `VITE_PUBLIC_POSTHOG_API_KEY` is set.
- Keep runtime labels, not-found behavior, and endpoint expectations aligned with the backend contract.
