# Frontend Architecture

This document describes the maintained architecture of the frontend under
`src/frontend/src/`.

The frontend is a React 19 + TypeScript + Vite app using TanStack Router for
routing, TanStack Query for backend-backed state, and Zustand for client-side
session/UI state.

## Current Directory Structure

```text
src/frontend/src/
├── app/                         # App bootstrap and root composition
├── routes/                      # File-based TanStack Router surfaces
├── screens/                     # Product slices and route-level screen modules
│   ├── shell/                   # App shell, navigation, auth/error screens
│   ├── workspace/               # Chat, transcript, workbench, inspector, artifacts
│   ├── volumes/                 # Volumes browser and file preview flows
│   └── settings/                # Runtime and app settings
├── components/
│   ├── ui/                      # Shared UI primitives
│   ├── prompt-kit/              # Prompt/message rendering components
│   └── shared/                  # Shared app-specific utilities/components
├── lib/
│   └── rlm-api/                 # REST/WebSocket client contract and generated API types
├── stores/                      # Cross-app Zustand stores
└── styles.css                   # Theme tokens and global styles
```

## Supported Product Surfaces

The live frontend shell supports only:

- `/app/workspace`
- `/app/volumes`
- `/app/settings`

Retired `taxonomy`, `skills`, `memory`, and `analytics` routes are no longer
supported compatibility entrypoints; unknown legacy paths now fall through to
the not-found flow.

## Routing and Screen Composition

- `src/router.tsx` owns the router instance.
- `src/routes/*` contains the file-based route tree.
- `src/routeTree.gen.ts` is generated and should not be edited by hand.
- Thin route wrappers should render screen modules from `src/screens/*` rather than
  introducing page-layer duplication.

The main frontend slices are:

- `src/screens/workspace/` for the dominant chat/runtime/workbench surface
- `src/screens/volumes/` for volumes browsing
- `src/screens/settings/` for runtime/app settings
- `src/screens/shell/` for shell chrome, navigation, and standalone auth/error screens

## State and Runtime Contract

- TanStack Query handles backend-backed reads and settings/status queries.
- Zustand holds local session, navigation, transcript, workbench, and shell state.
- `src/screens/workspace/model/chat-store.ts` remains part of the live streaming
  contract with the backend.
- `src/screens/workspace/model/run-workbench-store.ts` and
  `src/screens/workspace/model/run-workbench-adapter.ts` own workbench hydration.
- `src/screens/workspace/model/backend-chat-event-adapter.ts` reduces chat frames
  into transcript state.
- `src/screens/workspace/model/backend-artifact-event-adapter.ts` reduces execution
  steps into the artifact/graph view.

Runtime expectations:

- `/api/v1/ws/chat` is transcript-first.
- `/api/v1/ws/execution` is the canonical execution/workbench stream.
- `modal_chat` is the default runtime path and sends `execution_mode`.
- `daytona_pilot` is the Daytona-backed variant and sends `repo_url`, `repo_ref`,
  `context_paths`, and `batch_concurrency`.
- Frontend workbench state should hydrate from `execution_completed.summary`, not
  from Daytona-only chat-final scraping.

## Backend Integration

- `src/lib/rlm-api/client.ts` owns REST calls.
- `src/lib/rlm-api/wsClient.ts` owns chat/execution WebSocket setup.
- `src/lib/rlm-api/generated/openapi.ts` is generated from `openapi.yaml`.
- `src/lib/rlm-api/config.ts` derives REST/WS URLs from frontend env vars.

The frontend and backend contract is documented in
`docs/reference/frontend-backend-integration.md`. Treat that document and
`src/frontend/AGENTS.md` as the primary references when changing runtime labels,
request shapes, or websocket payload expectations.

## Validation

From `src/frontend/`:

- `pnpm install --frozen-lockfile`
- `pnpm run api:check`
- `pnpm run type-check`
- `pnpm run lint`
- `pnpm run test:unit`
- `pnpm run build`
- `pnpm run check`

If browser-level behavior changed, also run:

- `pnpm run test:e2e`
