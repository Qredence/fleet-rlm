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
- Zustand for ephemeral client state in `src/stores/`
- `src/stores/chatStore.ts` is part of the live streaming contract with the backend

Backend integration:

- `src/lib/rlm-api/client.ts` for REST calls
- `src/lib/rlm-api/wsClient.ts` and related websocket helpers for streaming
- `src/lib/rlm-api/generated/openapi.ts` for generated API types

Feature layout:

- `src/features/rlm-workspace/` for the main chat and runtime experience
- `src/features/artifacts/` for artifact canvas, graph, timeline, and REPL views
- `src/features/volumes/` for volume browsing
- `src/features/settings/` for settings UI
- `src/features/shell/` for app shell and navigation

Component layout:

- `src/components/ui/` for shared UI primitives
- `src/components/prompt-kit/` for AI SDK prompt and message components
- `src/components/chat/` for chat-specific controls
- `src/components/shared/` for shared utilities

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
