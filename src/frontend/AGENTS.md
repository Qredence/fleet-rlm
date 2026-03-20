# Frontend Agent Instructions

## Scope and Reading Order

This file is written for AI coding agents modifying the frontend app in `src/frontend/`.
Read the root [AGENTS.md](../../AGENTS.md) first for shared repo rules.
Consult [src/fleet_rlm/AGENTS.md](../fleet_rlm/AGENTS.md) when your frontend change touches backend routes, websocket payloads, runtime modes, or auth behavior.

Frontend source-of-truth files:

- `src/frontend/package.json` for scripts and validation
- `src/frontend/src/routes/*` for supported app surfaces and redirect behavior
- `src/frontend/src/lib/rlm-api/*` for REST and websocket integration
- `src/frontend/src/styles.css` for tokens and theme primitives
- `openapi.yaml` and `src/frontend/src/lib/rlm-api/generated/openapi.ts` for API contract alignment

## Agent Priorities

- Preserve the supported app surfaces: `RLM Workspace`, `Volumes`, and `Settings`.
- Keep legacy `taxonomy`, `skills`, `memory`, and `analytics` routes as redirects unless the shared product contract is intentionally changed.
- Do not hand-edit generated files like `src/routeTree.gen.ts` or `src/lib/rlm-api/generated/openapi.ts`.
- Keep runtime labels, websocket behavior, and request controls aligned with the backend contract.
- Prefer existing design tokens and component conventions over introducing one-off patterns.

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
- file-based routes under `src/routes/` define app surfaces and redirect behavior

State management:

- TanStack Query for backend-backed state
- Zustand for ephemeral client state, with cross-app shell state in `src/stores/`
- `src/screens/workspace/model/chat-store.ts` remains part of the live streaming contract with the backend
- Workspace-owned inspector/session state lives in `src/screens/workspace/model/workspace-ui-store.ts`
- Volumes file-selection state lives in `src/screens/volumes/model/volumes-selection-store.ts`
- `src/stores/navigation-types.ts` owns the shared `NavItem` type used by shell navigation and route helpers

Backend integration:

- `src/lib/rlm-api/client.ts` for REST calls
- `src/lib/rlm-api/wsClient.ts` and related websocket helpers for streaming
- `src/lib/rlm-api/generated/openapi.ts` for generated API types

Feature layout:

- `src/screens/workspace/` is the dominant product slice for chat, runtime, inspector, workbench, and workspace artifacts
- `src/screens/shell/` owns the route shell, shell chrome, and standalone auth/error screens
- `src/screens/volumes/` owns the volume browser, file preview, and file-selection helpers
- `src/screens/settings/` owns runtime and app settings
- Thin route wrappers under `src/routes/` should render screen modules rather than page-layer wrappers
- Shell code should consume workspace/volumes through top-level screen contracts like `workspace-shell-contract.ts`, `workspace-canvas-panel.tsx`, `volumes-shell-contract.ts`, and `volumes-canvas-panel.tsx` instead of reaching into deep `components/`, `model/`, or `hooks/` paths

Component layout:

- `src/components/ui/` for shared UI primitives
- `src/components/prompt-kit/` for AI SDK prompt and message components
- `src/screens/workspace/components/` for workspace-specific UI controls and message rendering
- `src/components/shared/` for shared utilities
- Prefer shadcn field composition (`FieldGroup`, `Field`, `FieldContent`, `FieldLabel`, `FieldDescription`, `FieldTitle`, `Switch`, `ToggleGroup`) over bespoke settings row wrappers when building forms
- Prefer behavior-bearing screen subcomponents over micro-wrappers; tiny layout-only wrappers should usually be inlined back into the screen component

## UI and Runtime Rules

Design/token rules:

- Theme primitives live in `src/styles.css`
- Use the existing semantic tokens and utility conventions instead of arbitrary color values
- Keep typography, icon sizing, spacing, and z-index usage aligned with the current design system

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

- `VITE_FLEET_WS_URL`
- `VITE_ENTRA_CLIENT_ID`
- `VITE_ENTRA_SCOPES`
- `VITE_PUBLIC_POSTHOG_API_KEY`
- `VITE_PUBLIC_POSTHOG_HOST`

Backend startup for frontend work:

- `uv run fleet-rlm serve-api --port 8000`

OpenAPI sync:

- `pnpm run api:sync` copies the root spec and regenerates frontend types
- `pnpm run api:check` verifies that committed generated artifacts match `openapi.yaml`
- If `api:check` produces formatting-only diffs in `openapi/fleet-rlm.openapi.yaml` or `src/lib/rlm-api/generated/openapi.ts`, keep those sync artifacts in the same change rather than hand-editing generated output

Lint and boundary enforcement:

- Frontend lint rules are configured in `src/frontend/vite.config.ts` via Vite+ (`vp`) overrides, not a standalone ESLint config
- `src/components/ui/*` and `src/components/prompt-kit/*` must not import from `src/screens/*`
- `src/screens/*/model/*` must not import from `src/screens/*/components/*`
- `src/screens/shell/*` must import workspace and volumes behavior through top-level screen contracts only

## Validation by Change Type

Fast frontend confidence:

- `pnpm install --frozen-lockfile`
- `pnpm run api:check`
- `pnpm run type-check`
- `pnpm run lint`
- `pnpm run test:unit`
- `pnpm run build`

Full frontend validation:

- `pnpm run check`

Use the backend AGENTS file or the root AGENTS file when you need wider validation for shared API or websocket contract changes.

## Agent Notes

- `components.json` defines the `@/*` alias and shadcn registry configuration.
- The dev server proxies `/api/v1` and `/health` to `localhost:8000`.
- PostHog initializes in `main.tsx` when `VITE_PUBLIC_POSTHOG_API_KEY` is set.
- Keep runtime labels, redirect behavior, and endpoint expectations aligned with the backend contract.
