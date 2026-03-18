# Frontend Guidelines

## Scope

This file covers the frontend app in `src/frontend/`.
Use the repo-wide [AGENTS.md](/Volumes/StorageBackup/_RLM/fleet-rlm-dspy/AGENTS.md) for shared workflow rules and [src/fleet_rlm/AGENTS.md](/Volumes/StorageBackup/_RLM/fleet-rlm-dspy/src/fleet_rlm/AGENTS.md) for backend-specific details.

## Tooling

- Package manager: `pnpm` (use `pnpm install --frozen-lockfile`)
- Build toolchain: Vite+ (`vp`) surfaced through npm scripts
- Framework: React 19 + TanStack Router + TanStack Query + Zustand

Canonical commands:

```bash
pnpm install --frozen-lockfile  # Install dependencies
pnpm run dev                    # Start dev server (proxies to localhost:8000)
pnpm run build                  # Production build
pnpm run type-check             # TypeScript check
pnpm run lint                   # Lint src/
pnpm run test:unit              # Unit tests (single pass)
pnpm run test:watch             # Unit tests (watch mode)
pnpm run test:coverage          # Unit tests with coverage
pnpm run test:e2e               # E2E tests (Playwright)
pnpm run api:sync               # Sync OpenAPI spec and generate types
pnpm run check                  # Full validation: types, lint, unit tests, build, e2e
```

Single test execution:

- Unit: `pnpm run test:unit src/path/to/__tests__/file.test.ts`
- E2E: `pnpm run test:e2e tests/e2e/file.spec.ts`

## High-Level Architecture

### Routing (TanStack Router)

File-based routing in `src/routes/` with auto-generated route tree:

- `src/router.tsx` - Router instance with generated `routeTree`
- `src/routeTree.gen.ts` - Auto-generated, do not edit
- Routes are file-based: `src/routes/app/workspace.tsx` → `/app/workspace`

App surface:

- `/app/workspace` - RLM Workspace (chat/runtime)
- `/app/volumes` - Volume browser
- `/app/settings` - Settings

Legacy routes (`taxonomy`, `skills`, `memory`, `analytics`) redirect to supported pages.

### State Management

- **TanStack Query**: Backend-backed state (API data, caching)
- **Zustand**: Ephemeral client state (`src/stores/`)
  - `chatStore.ts` - Chat streaming, messages
  - `artifactStore.ts` - Artifact UI state
  - `navigationStore.ts` - Navigation state
  - `themeStore.ts` - Theme preferences

### Backend Integration

All backend communication goes through `src/lib/rlm-api/`:

- `client.ts` - REST API client
- `wsClient.ts` - WebSocket client
- `wsReconnecting.ts` - Reconnecting WebSocket wrapper
- `wsFrameParser.ts` - Binary frame parsing
- `generated/openapi.ts` - Generated types (do not edit)

Backend endpoints:

- REST: `/health`, `/ready`, `/api/v1/auth/me`, `/api/v1/sessions/state`, `/api/v1/runtime/*`
- WebSocket: `/api/v1/ws/chat`, `/api/v1/ws/execution`

### Feature Structure

```
src/features/
  rlm-workspace/    # Main chat/runtime surface
    assistant-content/  # Assistant message rendering
    chat-shell/         # Chat container/layout
    message-inspector/  # Right-rail message details
    run-workbench/      # Daytona pilot workbench
  artifacts/        # Artifact canvas, graph, timeline, REPL
  settings/         # Settings pages
  volumes/          # Volume browser
  shell/            # App shell, navigation
```

### Components

- `src/components/ui/` - shadcn/ui primitives using @base-ui/react (with @radix-ui/react-slot for asChild pattern)
- `src/components/prompt-kit/` - AI SDK prompt components (message display, code blocks, attachments)
  - Renamed from `ai-elements` to align with OpenAI SDK conventions
  - Main exports: `Message`, `Conversation`, `PromptInput`, `CodeBlock`, `Tool`, `Task`, `Reasoning`
  - Components use AI SDK types (`Message`, `Attachment`) for seamless integration
  - Located wrappers preserve repo's `data-slot` conventions and compound component patterns
- `src/components/chat/` - Chat-specific components (ChatInput, runtime/execution mode dropdowns)
- `src/components/shared/` - Shared utilities

## UI & Styling

### Design Tokens

Theme defined in `src/styles.css` using OpenAI Apps SDK design tokens:

- Alpha primitives: `--alpha-02` through `--alpha-50`
- Semantic colors: `--color-text-*`, `--color-surface-*`, `--color-background-*`
- Typography: `--font-heading-*`, `--font-text-*`
- Radius: `--radius-*` (2xs to 4xl, full)
- Shadows: `--shadow-100` through `--shadow-400`

### Tailwind Conventions

- Typography: Never use font weights bolder than `font-medium`. Apply `tracking-tight` on main titles.
- Colors: Use custom palette tokens (`bg-primary-50`, `text-primary-900`). Never use arbitrary values or `bg-white`/`text-black`.
- Text: Use `text-balance` for headings, `text-pretty` for body, `tabular-nums` for data.
- Layout: Use `size-*` for square elements, `truncate`/`line-clamp` for dense UI.
- Z-index: Use fixed scale, no arbitrary `z-*`.
- Icons: `size={20}` and `strokeWidth={1.5}` consistently.

### React 19 Refs

Use regular `function` components with direct ref passing. React 19 supports refs as regular props, so `React.forwardRef` is not needed.

## Runtime UX Contract

Two runtime modes, backend-driven:

- **modal_chat** (default): Composer sends `execution_mode`. Standard right-rail with message inspector.
- **daytona_pilot** (experimental): Composer sends `repo_url`, `repo_ref`, `context_paths`, `batch_concurrency`. Switches to `RunWorkbench`.

Runtime labels are user-facing: "Modal chat" and "Daytona pilot".

## Environment

Create `.env` from `.env.example`:

```
VITE_FLEET_API_URL=http://localhost:8000
VITE_FLEET_WORKSPACE_ID=default
VITE_FLEET_USER_ID=fleetwebapp-user
VITE_FLEET_TRACE=true
```

Optional:

- `VITE_FLEET_WS_URL` - WebSocket URL override
- `VITE_ENTRA_CLIENT_ID`, `VITE_ENTRA_SCOPES` - Microsoft Entra SPA auth
- `VITE_PUBLIC_POSTHOG_API_KEY`, `VITE_PUBLIC_POSTHOG_HOST` - PostHog analytics

## Backend Startup

From the repo root:

```bash
uv run fleet-rlm serve-api --port 8000
```

## OpenAPI Type Sync

```bash
pnpm run api:sync   # Copies spec and generates types
pnpm run api:check  # Verifies sync (fails if drifted)
```

Generated: `src/lib/rlm-api/generated/openapi.ts` (do not edit).

## Validation

Full frontend validation:

```bash
pnpm install --frozen-lockfile
pnpm run api:sync
pnpm run api:check
pnpm run type-check
pnpm run lint
pnpm run test:unit
pnpm run build
pnpm run test:e2e
pnpm run check
```

## Notes

- `components.json` defines `@/*` alias and shadcn registry config.
- The dev server proxies `/api/v1` and `/health` to localhost:8000.
- PostHog analytics initialize in `main.tsx` when `VITE_PUBLIC_POSTHOG_API_KEY` is set.
- Keep runtime labels and endpoint contracts aligned with backend.
