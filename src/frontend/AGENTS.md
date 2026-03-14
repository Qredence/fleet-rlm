# Frontend AGENTS.md

## Purpose

This repository hosts the frontend app for the fleet-rlm ecosystem.

## Tooling

- Runtime/package manager: `bun`
- Install deps: `bun install`
- Dev server: `bun run dev`
- Build: `bun run build`
- Type check: `bun run type-check`
- Lint: `bun run lint`
- Robustness lint gate: `bun run lint:robustness`
- Unit tests: `bun run test:unit`
- E2E smoke tests: `bun run test:e2e`
- Full quality gate: `bun run check`

## shadcn & AI Elements

- `components.json` is the source of truth for shadcn and AI Elements registry wiring.
- Keep `components.json.aliases.utils` pointed at `@/lib/utils/cn`; do not reintroduce `src/components/ui/utils.ts`.
- The `@/* -> ./src/*` alias must live in `src/frontend/tsconfig.json` so `bunx --bun shadcn@latest info --json` resolves real frontend paths.
- Before refreshing registry-backed components, run `bunx --bun shadcn@latest info --json` and confirm `importAlias` is `@` and `resolvedPaths.utils` points to `src/lib/utils/cn`.
- Official AI Elements primitives live under `src/components/ai-elements/*`. Refresh them through shadcn/registry workflow instead of forking custom lookalikes.
- `src/components/chat/ChatInput.tsx` is a thin product wrapper around the official AI Elements `prompt-input` primitives. Keep execution-mode/settings affordances local, but do not recreate `src/components/chat/prompt-input/*`.

## Hook & Lib Ownership

- Keep `src/hooks/*` for active cross-cutting React hooks only. Delete zero-reference compatibility shims instead of parking deprecated hooks there.
- Deprecated navigation/history shims (`NavigationProvider`, `navigation-context`, `navigation-types`, `useNavigation`, `useChatHistory`, `useTheme`) are removed. Prefer `src/stores/navigationStore.ts` plus focused hooks like `useAppNavigate`.
- Keep `src/lib/*` for active adapters and shared utilities only. Removed mock/legacy modules (`lib/data/mock/skills.ts`, `lib/memory/metadata.ts`, `lib/skills/library.ts`) should stay deleted unless a real caller returns.

## Backend Integration

Core backend integration targets `fleet-rlm` at:
`<path-to-your-fleet-rlm-repo>`

### OpenAPI Sync Workflow

- Sync spec snapshot: `bun run api:sync-spec`
- Generate TS types: `bun run api:types`
- Full sync: `bun run api:sync`
- Drift check (must be clean): `bun run api:check`

Generated file policy:

- `src/lib/rlm-api/generated/openapi.ts` is generated and must not be edited manually.

## Frontend API Modules

- Sole backend layer: `src/lib/rlm-api/*`
- Legacy layer `src/lib/api/*` is removed and must not be reintroduced.

## Canonical Source Layout

- Route entrypoints and shells live in `src/app/*`.
- Reusable domain UI belongs in `src/features/*`.
- Shared presentational primitives live in `src/components/*`.
- Backend/data adapters live in `src/lib/*`.
- Cross-cutting React hooks live in `src/hooks/*`; Zustand stores live in `src/stores/*`.
- `src/screens/*` is not a general-purpose home for new work. The remaining chat files there are transitional and should only be touched when continuing that refactor.
- Remove empty folders and dead placeholder modules instead of leaving parallel directory schemes behind.

### API Layer Ownership

- Use `src/lib/rlm-api/*` for all backend contracts (`/health`, `/ready`, `/api/v1/sessions/state`, `/api/v1/runtime/*`, `/api/v1/ws/chat`, `/api/v1/ws/execution`).
- New frontend data work must map to existing FastAPI endpoints or be gated as unsupported in UI.
- Supported product surfaces are `workspace`, `volumes`, and `settings`.
- Legacy `taxonomy`, `skills`, `memory`, and `analytics` URLs should redirect to canonical supported routes instead of remaining in primary navigation.

### State Ownership

- Use TanStack Query for backend-backed server state.
- Use Zustand only for ephemeral client state (streaming/chat/session UI state, artifact canvas state, or explicitly demo/mock-only state).
- Do not add new feature state to generic app-wide providers when it can live inside a domain module.

### Mock Data

- Canonical mock data lives under `src/lib/data/mock/*`.
- Import mock data directly from those modules; do not reintroduce compatibility barrels like `src/lib/data/mock-skills.ts`.
- If a screen is mock-only, label it clearly in code and UI instead of silently degrading from a removed backend route.

### Runtime Conventions

- Route modules are lazy-loaded through `src/lib/perf/lazyWithRetry.ts` and `src/lib/perf/routePreload.tsx`.
- Navigation preloads likely next routes on intent (`TopHeader`, `mobile-tab-bar`) to reduce first-click latency.
- Router errors must render `RouteErrorPage` (never rely on React Router’s default crash screen).
- The RLM Workspace chat flow should use backend runtime only (no legacy API fallback path).
- Canonical domain ownership now lives in `src/features/rlm-workspace/*` for chat/runtime UX and `src/features/volumes/*` for the Modal Volume browser; route pages under `src/app/pages/*` should stay thin.
- Assistant-turn composition belongs in `src/features/rlm-workspace/assistant-content/*`; keep `ChatMessageList.tsx` as the conversation shell / standalone trace renderer and `chatDisplayItems.ts` as the grouping + attachment boundary.
- Keep the chat column as the primary live reasoning surface: current-turn reasoning, trajectory, and attachable execution traces should fold into the active assistant turn in real time instead of waiting for the inspector or rendering as detached standalone rows.
- The workspace right rail is a message-scoped `Message Inspector`, not a second chat surface. Keep it closed by default, scoped to the selected assistant turn, and driven from the same normalized assistant-content model used in chat.
- Inline assistant content is summary-first: chat shows the answer, summary pills, and compact trajectory/execution/evidence previews; full-detail inspection belongs in `src/features/rlm-workspace/message-inspector/*`.
- Do not silently clamp or ellipsize primary reasoning copy in chat or the inspector. Let trajectory cards grow vertically, and keep scrolling at the conversation/panel level instead of clipping the reasoning body inside the card.
- Inspector tab order is fixed: `Trajectory`, `Execution`, `Evidence`, then `Graph`. Only surface `Graph` when execution artifacts show meaningful branching, delegation, or lineage worth visualizing.
- Turn-scoped execution graph state persists via `turnArtifactsByMessageId` in chat/history stores. Preserve backward-compatible history hydration when evolving this shape.
- Product-facing labels should use `RLM Workspace` and `Volumes`; do not introduce new `skill-creation` or `taxonomy` product terminology.
- Keep `src/components/ui/*` limited to primitives and thin wrappers. Shell navigation or workspace composition widgets belong under `src/features/*` or another shared domain folder.

## Environment Variables

- `VITE_FLEET_API_URL`
- `VITE_FLEET_WS_URL`
- `VITE_FLEET_WORKSPACE_ID`
- `VITE_FLEET_USER_ID`
- `VITE_FLEET_TRACE`
- `VITE_ENTRA_CLIENT_ID`
- `VITE_ENTRA_AUTHORITY`
- `VITE_ENTRA_SCOPES`
- `VITE_ENTRA_REDIRECT_PATH`
- Default Entra authority is `https://login.microsoftonline.com/organizations`; use `VITE_ENTRA_AUTHORITY` only when you intentionally need an override.

## Validation Expectations

Before finishing backend-integration changes, run: 0. `bun install`

1. `bun run api:sync`
2. `bun run api:check`
3. `bun run type-check`
4. `bun run lint:robustness`
5. `bun run test:unit`
6. `bun run build`
7. `bun run test:e2e`
8. `bun run check`

Focused validation for chat/composer and cleanup work:

1. `bun run type-check`
2. `bun run build`
3. `bun run test:unit -- src/app/layout/__tests__/BuilderPanel.creation-tabs.test.tsx src/app/layout/__tests__/BuilderPanel.file-detail.test.tsx src/components/chat/__tests__/ChatInput.test.tsx src/components/chat/input/__tests__/AttachmentDropdown.test.tsx src/components/chat/input/__tests__/ExecutionModeDropdown.test.tsx src/components/chat/input/__tests__/SettingsDropdown.test.tsx src/features/rlm-workspace/__tests__/RlmWorkspace.runtime-warning.test.tsx src/features/rlm-workspace/__tests__/chatDisplayItems.test.ts src/features/rlm-workspace/__tests__/ChatMessageList.ai-elements.test.tsx src/features/rlm-workspace/__tests__/backendChatEventAdapter.test.ts src/features/rlm-workspace/message-inspector/__tests__/MessageInspectorPanel.test.tsx src/hooks/__tests__/useAppNavigate.test.ts src/stores/__tests__/chatHistoryStore.test.ts src/stores/__tests__/navigationStore.test.ts`
4. `bunx --bun shadcn@latest info --json`

## Dependency Policy

- Curated dependency refreshes can move low-risk packages forward in bulk (Vitest, Radix UI, Tailwind/PostCSS, CodeMirror patches, PostHog patches, `shiki`, `globals`, `typescript-eslint`, `tw-animate-css`).
- Defer high-risk majors that expand scope into separate follow-ups. Current deferred majors are `eslint`/`@eslint/js` v10, `eslint-plugin-react-hooks` v7, `eslint-plugin-react-refresh` v0.5, `jsdom` v28, `openapi-typescript` v7, and `react-resizable-panels` v4.
