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

### UI & Styling

- **Typography**: Never use font weights bolder than `font-medium`. Apply small negative tracking (`tracking-tight` or similar) on main titles.
- **Colors**:
  - Use the custom Tailwind palette (e.g., `bg-primary-50`, `text-primary-900`).
  - Never use arbitrary color values.
  - Avoid `bg-white`, `bg-black`, `text-white`, `text-black`, and `outline-black`; use primary palette tokens instead.
- **Markdown Titles**: Avoid top margin on markdown headings.
- MUST use text-balance for headings and text-pretty for body/paragraphs
- MUST use tabular-nums for data
- SHOULD use truncate or line-clamp for dense UI
- NEVER modify letter-spacing (tracking-\*) unless explicitly requested
- MUST use a fixed z-index scale (no arbitrary z-\*)
- SHOULD use size-_ for square elements instead of w-_ + h-\*
- **Icons**:
  - All icons should use `size={20}` and `strokeWidth={1.5}` consistently
- **React 19 Refs**: Use regular `function` components with direct ref passing instead of `React.forwardRef` (React 19 supports refs as regular props)


## App Surface

The canonical route surface is:

- `/app/workspace`
- `/app/volumes`
- `/app/settings`




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


## Notes

- Keep `components.json` and the `@/*` alias wiring valid for registry-backed component workflows.
- Document `pnpm` commands as the contributor surface even though the scripts ultimately invoke Vite+ tooling.
- When route/runtime/product terminology changes, update this file so it stays aligned with the live app rather than preserving historical names.
