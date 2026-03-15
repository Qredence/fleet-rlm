# Frontend Guidelines

## Scope

This file covers the frontend app in `src/frontend/`.
Use the repo-wide [AGENTS.md](/Volumes/StorageBackup/_RLM/fleet-rlm-dspy/AGENTS.md) for shared workflow rules and [src/fleet_rlm/AGENTS.md](/Volumes/StorageBackup/_RLM/fleet-rlm-dspy/src/fleet_rlm/AGENTS.md) for backend-specific details.

## Tooling

- Package manager: `pnpm`
- Build/runtime toolchain: Vite+ (`vp`) underneath the npm scripts
- Framework: React 19 + React Router 7 + TanStack Query + Zustand

Canonical commands:

- `pnpm install --frozen-lockfile`
- `pnpm run dev`
- `pnpm run build`
- `pnpm run preview`
- `pnpm run type-check`
- `pnpm run lint`
- `pnpm run test:unit`
- `pnpm run test:e2e`
- `pnpm run api:sync`
- `pnpm run api:check`
- `pnpm run check`

Direct `vp` usage is optional shorthand for contributors already using Vite+ locally. In this repo, the primary documented workflow is `pnpm run ...`.

## App Surface

The canonical route surface is:

- `/app/workspace`
- `/app/volumes`
- `/app/settings`

Legacy routes such as `/app/taxonomy`, `/app/skills`, `/app/memory`, and `/app/analytics` redirect to supported pages. Do not document them as active disabled product surfaces.

## Ownership Map

- `src/app/`: router, shell layout, page entrypoints, providers
- `src/features/rlm-workspace/`: chat/runtime UX, assistant content, message inspector, Daytona run workbench
- `src/features/volumes/`: runtime-backed volume browser
- `src/features/settings/`: runtime settings, diagnostics panes, settings dialog/page
- `src/features/shell/`: app chrome such as user menu, command palette, dialogs
- `src/components/ai-elements/`: shared AI Elements primitives
- `src/components/chat/`: composer wrapper and chat input controls
- `src/components/ui/`: presentational primitives and thin wrappers
- `src/lib/rlm-api/`: canonical backend contract layer
- `src/lib/auth/`: MSAL/Entra session handling and token storage
- `src/lib/telemetry/`: PostHog frontend telemetry
- `src/stores/`: Zustand client state

Keep route pages thin. New product behavior should usually live under `features/`.

## Runtime UX Contract

The chat/runtime split is product-visible and must stay aligned with the backend:

- `modal_chat`
  - default runtime mode
  - composer may send `execution_mode`
  - standard right-rail experience centers on the message inspector
- `daytona_pilot`
  - experimental runtime mode
  - composer may send optional `repo_url`, `repo_ref`, `context_paths`, and `batch_concurrency`
  - `execution_mode` is not sent
  - the builder panel switches to the dedicated `RunWorkbench`

Additional expectations:

- The main chat surface remains shared between Modal and Daytona sessions.
- Standard assistant inspection belongs in the `Message Inspector`.
- Daytona-specific iterations, prompts, callbacks, evidence, and final output belong in `run-workbench/`.
- Keep the runtime labels user-facing as `Modal chat` and `Daytona pilot`.

## Backend Contract

The frontend is backend-driven and should stay aligned with these surfaces:

- `/health`
- `/ready`
- `GET /api/v1/auth/me`
- `GET /api/v1/sessions/state`
- `/api/v1/runtime/settings`
- `/api/v1/runtime/status`
- `/api/v1/runtime/tests/modal`
- `/api/v1/runtime/tests/lm`
- `/api/v1/runtime/volume/tree`
- `/api/v1/runtime/volume/file`
- `POST /api/v1/traces/feedback`
- `/api/v1/ws/chat`
- `/api/v1/ws/execution`

Generated contract files:

- `openapi/fleet-rlm.openapi.yaml`
- `src/lib/rlm-api/generated/openapi.ts`

Do not hand-edit generated OpenAPI output.

## State and Data Ownership

- Use TanStack Query for backend-backed state.
- Use Zustand for ephemeral client-side state such as chat streaming, artifact UI state, navigation state, and Daytona workbench state.
- Keep backend communication inside `src/lib/rlm-api/*`.
- Keep auth token/session logic inside `src/lib/auth/*`.

## Environment Contract

Primary frontend env vars:

- `VITE_FLEET_API_URL`
- `VITE_FLEET_WORKSPACE_ID`
- `VITE_FLEET_USER_ID`
- `VITE_FLEET_TRACE`
- `VITE_MOCK_MODE`

Optional overrides:

- `VITE_FLEET_WS_URL`
  - optional websocket override
  - if unset, websocket URLs are derived from `VITE_FLEET_API_URL` or the current browser origin

Microsoft Entra SPA auth:

- `VITE_ENTRA_CLIENT_ID`
- `VITE_ENTRA_AUTHORITY`
- `VITE_ENTRA_SCOPES`
- `VITE_ENTRA_REDIRECT_PATH`

PostHog frontend telemetry:

- `VITE_PUBLIC_POSTHOG_API_KEY`
- `VITE_PUBLIC_POSTHOG_HOST`

The frontend now uses real Entra bootstrap behavior when configured: initialize MSAL, acquire/store an access token, and call `GET /api/v1/auth/me`.

## Validation

Recommended frontend validation flow:

1. `pnpm install --frozen-lockfile`
2. `pnpm run api:sync`
3. `pnpm run api:check`
4. `pnpm run type-check`
5. `pnpm run lint`
6. `pnpm run test:unit`
7. `pnpm run build`
8. `pnpm run test:e2e`
9. `pnpm run check`

Focused workspace/runtime validation:

- `pnpm run test:unit src/features/rlm-workspace/__tests__/backendChatEventAdapter.test.ts src/features/rlm-workspace/__tests__/ChatMessageList.ai-elements.test.tsx src/features/rlm-workspace/__tests__/RlmWorkspace.daytona-workbench.test.tsx src/features/rlm-workspace/__tests__/RlmWorkspace.runtime-warning.test.tsx src/features/rlm-workspace/__tests__/useBackendChatRuntime.daytona-error.test.tsx src/features/rlm-workspace/run-workbench/__tests__/runWorkbenchAdapter.test.ts src/features/rlm-workspace/run-workbench/__tests__/RunWorkbench.test.tsx src/components/chat/__tests__/ChatInput.test.tsx src/components/chat/input/__tests__/RuntimeModeDropdown.test.tsx src/components/chat/input/__tests__/ExecutionModeDropdown.test.tsx src/components/chat/input/__tests__/SettingsDropdown.test.tsx`

## Notes

- Keep `components.json` and the `@/*` alias wiring valid for registry-backed component workflows.
- Document `pnpm` commands as the contributor surface even though the scripts ultimately invoke Vite+ tooling.
- When route/runtime/product terminology changes, update this file so it stays aligned with the live app rather than preserving historical names.
