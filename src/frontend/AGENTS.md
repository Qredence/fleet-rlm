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
4. Preserve supported surfaces: **Workbench**, **Optimization** (`/app/optimization`), **Volumes**, **Settings**.
5. Keep retired paths (`taxonomy`, `skills`, `memory`, `analytics`) falling through to `/404`.

---

## Source-of-Truth Files

| Concern                 | File(s)                                                                 |
| ----------------------- | ----------------------------------------------------------------------- |
| Scripts & validation    | `package.json`                                                          |
| Lint/build/import rules | `vite.config.ts`                                                        |
| Style token guard       | `scripts/check-style-tokens.mjs`                                        |
| Routes & surfaces       | `src/routes/*`                                                          |
| Client entry/hydration  | `src/client.tsx`                                                        |
| App chrome / layout     | `src/features/layout/*`                                                 |
| Product surfaces        | `src/features/{workspace,optimization,volumes,settings}/index.ts`       |
| UI primitives           | `src/components/ui/*` (shadcn/Base UI)                                  |
| Agent Elements (chat)   | `src/components/agent-elements/*`                                       |
| Product compositions    | `src/components/product/*`                                              |
| Auth/session restore    | `src/lib/auth/auth-provider.tsx`, `src/features/layout/app-sidebar.tsx` |
| API clients & types     | `src/lib/rlm-api/*`                                                     |
| Workspace adapters      | `src/lib/workspace/*`                                                   |
| Theme / tokens          | `src/styles/globals.css`, `src/components/agent-elements/agent-ui.css`  |
| shadcn config           | `components.json`                                                       |
| API contract            | `openapi.yaml`, `src/lib/rlm-api/generated/openapi.ts`                  |
| Dead-code analysis      | `knip.json`                                                             |

### Generated / Synced — Do Not Hand-Edit

- `src/routeTree.gen.ts`
- `src/lib/rlm-api/generated/openapi.ts`
- `openapi/fleet-rlm.openapi.yaml`
- `dist/`

---

## Architecture

### Component Layers (outer → inner)

1. **`src/components/ui/*`** — shadcn/Base UI primitives. Thin, semantic, no feature imports. Includes `code-block.tsx` (both simple `CodeBlock`/`CodeBlockCode` and rich `CodeBlockViewer`/`CodeBlockHeader`/`CodeBlockFilename`/`CodeBlockCopyButton`/`CodeBlockActions`/`CodeBlockContent`).
2. **`src/components/agent-elements/*`** — **Canonical agent/chat UI** ([Agent Elements](https://agent-elements.21st.dev/docs) shadcn registry). `AgentChat`, `InputBar`, tool cards, `UIMessage`-shaped transcripts.
3. **`src/components/product/*`** — Reusable product compositions (empty states, skeletons, panels, shared recipes). Do not add chat, reasoning, or tool transcript UI here; use Agent Elements.
4. **`src/features/layout/*`** — App chrome. Consumes workspace/volumes/settings through feature entrypoints only.
5. **`src/features/{workspace,optimization,volumes,settings}/*`** — Canonical surface ownership with `index.ts` as the public contract.
6. **`src/lib/{rlm-api,workspace}/*`** — API clients, adapters, stores, frame shaping.
7. **`src/stores/*`** — Cross-app shell/layout and navigation state.

### Restructuring Invariants

The following layers were removed and must not be reintroduced:

- **Legacy AI Elements layer** — **deleted**. All primitives have been either merged into `ui/` (code-block), rewritten on `agent-elements/` (trajectory-chain), or removed as dead code (prompt-input, chain-of-thought, reasoning). Do not install legacy AI registry components.
- **Legacy workspace composer layer** — **deleted**. The orphaned composer wrapper and its legacy prompt input dependency have been removed. The canonical composer is `InputBar` from `agent-elements/input-bar.tsx`.
- **`src/screens`** — never existed. The stale `@/screens/*` lint rule has been removed from `vite.config.ts`.
- **`components/tool-ui`** — retired. Tool transcript UI belongs under `components/agent-elements/tools/*`.

### Chat data flow (not Vercel `useChat`)

The backend streams custom websocket frames. The frontend does **not** call `useChat()` directly:

```
backend WS frames
  → lib/workspace/backend-chat-event-adapter.ts
  → lib/workspace/backend-artifact-event-adapter.ts
  → features/workspace/conversation/agent-chat-adapter.ts (UIMessage + toolRenderers)
  → AgentChat (agent-elements)
```

### Import Boundaries (enforced in `vite.config.ts`)

- `src/components/{ui,agent-elements,product}/*` **must not** import from `src/features/*`.
- `src/lib/workspace/*` **must not** depend on workspace UI modules.
- `src/routes/*` **must** import feature entrypoints, not deep feature modules.
- `src/features/layout/*` **must** consume workspace/volumes/settings through their feature entrypoints or explicit public contracts.
- `@/lib/utils` is the canonical `cn()` import path.

### Route Ownership

- `src/router.tsx` owns the router instance.
- `src/routes/` defines file-based routes. Keep route wrappers thin; compose feature entry modules through `src/features/*/index.ts`.
- `src/routeTree.gen.ts` is generated.

### Route Data and Hydration

- Client entry `src/client.tsx` owns the static/SSR hydration split. Keep the static path on
  `createRoot`, keep SSR hydration on `hydrateRoot`, preserve the `window.__hydrated` test signal,
  and leave `PostHogProvider` mounted unconditionally.
- Route loaders for backend-backed surfaces should await TanStack Query work before returning.
  Prefer shared `queryOptions(...)` factories with `queryClient.ensureQueryData(...)` or
  `queryClient.prefetchQuery(...)` so loaders and hooks share keys, functions, and type inference.
- Do not leave route-loader prefetches floating. Empty-cache transitions should not flicker or
  reflow because a route rendered before its blocking data was scheduled.

### Workspace Structure

Responsibility folders under `src/features/workspace/`:

- `screen/` — route entry
- `conversation/` — chat rendering
- `sidepanel/` — workspace-local sidepanel, trace fallback, graph, and volume tabs
- `inspection/` — detail panels (trajectory-chain uses agent-elements primitives, not chain-of-thought)
- `workbench/` — execution trace / workbench
- `session/` — session management

Assistant transcript/content modeling belongs under:
`src/features/workspace/conversation/assistant-content/model/`

### Workspace Sidepanel Contract

Workspace chat is the primary surface. The workspace sidepanel is
workspace-local, collapsible, and resizable; do not promote it into the global
route shell or replace `/app/volumes`.

Supported sidepanel tabs are exactly:

- `Trajectories`
- `Graph`
- `Volume`

`Trajectories` and `Graph` resolve session traces by durable chat session id
first and by runtime `external_session_id` when present. If MLflow traces are
missing or unavailable, they must fall back to live transcript and artifact
data instead of showing a hard-empty trace state.

`Volume` uses the Daytona volume APIs for the current workspace/session. It
supports inline file preview inside the workspace sidepanel and a resizable
tree/preview split. The routed `/app/volumes` page remains the full-page
durable volume browser and should not be collapsed into the workspace
sidepanel.

### Session Persistence and Restore

- `workspace-screen.tsx` owns first-message chat session creation and uses `lastSavedStateRef` to
  avoid redundant state writes.
- `use-workspace-runtime.ts` captures `db_session_id` from `execution_started.summary` and binds
  local workspace state to the durable backend session id.
- `app-sidebar.tsx` starts authenticated background session sync. On logout or token expiration,
  clear TanStack Query state before showing another tenant/user's cached session data.
- Keep FastAPI plus `FleetRepository` as the runtime authorization boundary; do not move product
  session reads or writes to direct Neon Data API calls from the browser.

### Neon Auth UI

- Branded login/signup pages render granular Neon Auth forms (`SignInForm`, `SignUpForm`) directly.
  Keep catch-all routes `auth.$pathname.tsx` and `account.$pathname.tsx` for Neon internal flows.
- Strip default Neon form chrome with
  `classNames={{ base: "border-0 bg-transparent p-0 shadow-none w-full !max-w-none" }}` when forms
  are embedded in Fleet layouts.
- `auth-provider.tsx` owns token refresh and query-cache clearing. Keep proactive refresh before
  profile/session sync so display names and admission state update without a manual reload.
- `typed-client.ts` must normalize API base URLs without trailing slashes to avoid duplicate slashes,
  routing, or CORS mismatches.

**Do not** create feature-local `ui/` folders; `src/components/ui/*` is the only primitive `ui` namespace.

---

## Tech Stack

- **Package manager:** `pnpm` 11.8.0 from `package.json` (always `pnpm install --frozen-lockfile`)
- **Build / lint / format:** Vite+ (`vp`) via `pnpm run ...`
- **Framework:** React 19 + TypeScript 5.9+
- **Router:** TanStack Router (file-based)
- **State:** Zustand + TanStack Query
- **Styling:** Tailwind CSS v4 + `tw-animate-css` + `@theme inline`
- **Testing:** Vitest (unit), Playwright (e2e)
- **Dead-code analysis:** knip (`pnpm run lint:dead-code`)

Do not add legacy pnpm build-trust fields such as `onlyBuiltDependencies` or
`trustedDependencies` to `package.json`; keep package-manager security settings in the current
pnpm-supported config surface when one is introduced.

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
pnpm run lint:robustness     # alias for lint
pnpm run lint:style-tokens   # style token guard (arbitrary values + raw palette colors)
pnpm run lint:dead-code      # knip — unused files, exports, dependencies
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

# Dead-code analysis
pnpm run lint:dead-code      # knip — find unused files, exports, dependencies

# Full validation
pnpm run check               # type-check + lint + lint:style-tokens + lint:dead-code + test:unit + build + test:e2e
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
pnpm run lint:style-tokens
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

- Theme primitives live in `src/styles/globals.css` and `src/components/agent-elements/agent-ui.css`. Keep the Tailwind v4 baseline canonical.
- Use **semantic tokens and shared variants** — avoid arbitrary colors or local token layers.
- **Eliminate arbitrary Tailwind values**. The project maintains token-backed `@utility` classes for all font sizes, radii, and common dimensions. Do not introduce new `text-[Npx]`, `w-[Npx]`, `h-[Npx]`, `rounded-[Npx]`, `leading-[...]`, or `tracking-[...]` values. If a size is missing, add a design token and `@utility` in `globals.css` or `agent-ui.css` rather than using an arbitrary value.
- For analytical canvases, use direct absolute markdown links to `.canvas.tsx` files in chat/reporting
  output so the IDE Canvas opens reliably.
- Align complex dashboards with base UI/shadcn primitives: standard `text-sm` tabs, `variant="elevated"`
  cards for major containers, and standard `h-9` form controls unless a shared token utility exists.
- Preserve focus indicators on core forms such as `input-bar.tsx`; sidebar-specific focus suppression
  belongs only under targeted sidebar selectors.
- Generic tree primitives such as `TreeView` must implement keyboard tree behavior (`role="tree"`,
  focus management, arrow keys, Enter/Space, Home/End) and keep mouse clicks synchronized with
  `focusedId`.
- Target visual JSON wrapping with scoped selectors such as `.visual-json-tree`; never add global
  `[role="tree"]` CSS that could affect filesystem trees or other ARIA tree widgets.

### Style Token Enforcement

A CI guard (`pnpm run lint:style-tokens`) automatically fails when banned patterns are detected:

- **Banned**: `text-[Npx]`, `rounded-[Npx]`, `bg-(red|emerald|amber|blue|green|yellow)-500`, `text-(red|emerald|amber|blue|green|yellow)-N`, `border-(red|emerald|amber|blue|green|yellow)-500`, `dark:text-(red|emerald|amber|blue|green|yellow)-N`
- **Allowed**: token-bridge forms like `text-[length:var(--…)]`, `rounded-[calc(var(--…))]`, `rounded-[inherit]`
- **Exception**: `src/features/settings/screen/settings-content.tsx` — theme-swatch illustrations use raw `bg-zinc-*` values (documented with a `theme-swatch:` comment)

### Typography utilities (use these instead of arbitrary font sizes)

- `typo-micro` — 8px (`text-3xs`)
- `typo-helper` — 10px (`text-2xs`)
- `typo-body-xs` — 11px
- `typo-caption` — 12px (`text-xs`)
- `typo-body-sm` — 13px
- `typo-label` / `typo-label-regular` — 14px (`text-sm`)
- `typo-base` — 14px (base body)
- `typo-composer` — 14px / 1.6 line-height (chat composer textarea)
- `typo-display` — 32px (`text-[2rem]`)
- `tracking-tight-custom` — `-0.18px` (sidebar, composer)
- `tracking-tighter-custom` — `-0.05em` (display headings)
- `tracking-wide-custom` — `0.12em` (uppercase labels)
- `tracking-wider-custom` — `0.08em` (uppercase mono labels)
- `leading-loose-custom` — `1.7142857` (file preview line numbers)

### Layout dimension utilities

- `max-w-4/5` — `80%`
- `max-w-message` — `95%`
- `max-w-skeleton` — `280px`
- `max-w-drawer-sm` — `200px`
- `max-w-drawer-xs` — `180px`
- `max-w-attachment` — `200px` (file attachment chips)
- `max-w-sidebar-label` — `120px` (sidebar nav item labels)
- `min-w-optimization-table` — `1080px` (optimization run history table)
- `w-select-xl` — `132px`
- `w-sheet-optimization` — `min(980px, 92vw)` (optimization run details sheet)

### Height utilities

- `h-info-bar` / `max-h-info-bar` — `34px` (input bar info strip)
- `h-skeleton-row` — `28px` (skeleton loading rows)
- `h-skeleton-row-lg` — `48px` (large skeleton loading rows)
- `min-h-touch` — `44px` (touch-target minimum height)

### Radius utilities (agent-ui.css)

- `rounded-an-action-sm` — `4px` (small action buttons)
- `rounded-an-action-md` — `6px` (medium action buttons, menu items)
- `rounded-an-action-lg` — `8px` (popover surfaces, error message cards)

### Status color tokens (auto dark-mode via `@theme inline`)

- `bg-success` / `text-success` / `border-success` — green (completed, success)
- `bg-warning` / `text-warning` / `border-warning` — amber (needs review, warning)
- `bg-danger` / `text-danger` / `border-danger` — red (error, failed)
- All support opacity modifiers: `bg-success/10`, `border-warning/30`, etc.

### Shadow utilities

- `shadow-sidebar-ring` — `0 0 0 1px hsl(var(--sidebar-border))` (sidebar outline variant)

### Runtime-driven styles

Dynamic colors (e.g. from `STEP_TYPE_META`) must use CSS custom properties set via the `style` prop, consumed by `@utility` classes in `globals.css`. Do not use inline `style={{ color: ..., backgroundColor: ... }}` for repeated patterns. Example:

```tsx
<div style={{ "--node-color": meta.color } as React.CSSProperties} className="node-color-text node-tint">
```

### Shared visual recipes

Shared visual recipes belong in `src/components/product/*` or `src/components/agent-elements/input/*`, not duplicated locally. Current recipe components:

- `NodeBadge` — small badge/pill for graph nodes and execution metadata
- `TreeView` — keyboard-accessible product tree primitive for volume/filesystem-style trees
- `ToolActionButton` — CVA primary/ghost/ghostSoft × sm/md/mdWide button for tool cards, approval footers, and question prompts
- `CenteredErrorShell` — card-wrapped full-height centered error/empty-state shell (404, route-error-screen)
- `PopoverSurface` / `popoverSurfaceClass` — shared popover surface recipe for input-bar, model-picker, mode-selector

Preserve shell/layout root stacking context for portaled overlays.

---

## React & Runtime Rules

- Prefer **React 19 direct ref passing** over `forwardRef` by default.
- `daytona_pilot` is the public runtime label. Request controls: `execution_mode`, `repo_url`, `repo_ref`, `context_paths`, `batch_concurrency`.
- Runtime labels shown to users should describe the Daytona-backed workbench path only.
- Shared runtime status queries: `src/hooks/runtime/use-runtime-status.ts`.
- Generic browser/UI hooks live in `src/hooks/ui/*`.
- The **Volumes** surface represents mounted durable storage, not the transient live workspace.

## Naming Conventions

- New handwritten feature files: `kebab-case`
- React components: `PascalCase`
- Hooks: `useThing`
- Framework exceptions preserved: `App.tsx`, `__root.tsx`, `$.tsx`

## Testing Conventions

- Colocate tests under `__tests__/` when practical.
- Tests for `src/lib/workspace/*` and `src/features/workspace/{conversation,inspection,screen,sidepanel,session,workbench}/*` should import owners directly, not via route wrappers or compatibility barrels.

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
VITE_FLEET_WORKSPACE_ID
VITE_FLEET_USER_ID
VITE_AGENTATION_ENDPOINT
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

## Agent Elements and shadcn Registries

[Agent Elements](https://agent-elements.21st.dev/docs) is the **canonical chat/agent UI kit**. Official guidance: **do not mix** Agent Elements with CopilotKit, `@ai-elements`, `@prompt-kit`, or other chat kits for message/tool surfaces.

Registries are configured in [components.json](components.json):

| Namespace         | URL                                             | Use                               |
| ----------------- | ----------------------------------------------- | --------------------------------- |
| `@agent-elements` | `https://agent-elements.21st.dev/r/{name}.json` | Chat shell, tool cards, input bar |
| `@prompt-kit`     | `https://www.prompt-kit.com/c/{name}.json`      | Avoid unless net-new capability   |

> The `@ai-elements` registry is no longer configured. Do not install or import from `@ai-elements`.

### Install Agent Elements

```bash
# Preferred: namespaced registry alias from components.json
npx shadcn@latest add @agent-elements/agent-chat

# Or direct URL (agent-chat pulls transitive deps)
npx shadcn@latest add https://agent-elements.21st.dev/r/agent-chat.json
```

Import from the **exact file** (no barrel):

```tsx
import { AgentChat } from "@/components/agent-elements/agent-chat";
import { BashTool } from "@/components/agent-elements/tools/bash-tool";
```

### Canonical Agent Elements (actively used)

| Component           | Location                                             | Consumers                                               | Purpose                                                                       |
| ------------------- | ---------------------------------------------------- | ------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `AgentChat`         | `agent-elements/agent-chat.tsx`                      | `workspace-message-list.tsx`                            | Full chat shell (messages + input)                                            |
| `InputBar`          | `agent-elements/input-bar.tsx`                       | `workspace-agent-input-bar.tsx`                         | Composer with mode/model pickers                                              |
| `Suggestions`       | `agent-elements/input/suggestions.tsx`               | `AgentChat` via `workspace-message-list.tsx`            | Quick-prompt chips (empty state + input)                                      |
| `PopoverSurface`    | `agent-elements/input/popover-surface.tsx`           | `input-bar`, `model-picker`, `mode-selector`            | Shared popover surface recipe                                                 |
| Tool mapping        | `lib/workspace/agent-tool-parts.ts`                  | `agent-chat-adapter.ts`, `execution-inspector-rows.tsx` | Shared tool part normalization                                                |
| Static tool helpers | `agent-elements/utils/static-tool-parts.ts`          | workbench, inspector                                    | `ThinkingTool` steps outside chat transcripts                                 |
| Tool cards          | `agent-elements/tools/*`                             | `agent-chat-adapter.ts` via `toolRenderers`             | Bash, Edit, Search, MCP, Subagent, Thinking, Generic                          |
| `TextShimmer`       | `agent-elements/text-shimmer.tsx`                    | tool rows, loading states                               | Streaming label shimmer                                                       |
| `Streamdown`        | `ui/streamdown.tsx`                                  | agent-elements markdown                                 | Canonical markdown renderer                                                   |
| `CodeBlockViewer`   | `ui/code-block.tsx`                                  | `bash-tool.tsx`                                         | Rich code block with header, filename, copy button                            |
| `TrajectoryChain`   | `features/workspace/inspection/trajectory-chain.tsx` | `trajectory-tab`, `trajectory-inspector-tab`            | Collapsible step chain (uses agent-elements primitives, not chain-of-thought) |

Wire backend tools through `agent-chat-adapter.ts` → `ToolRenderer`. Shared normalization lives in `lib/workspace/agent-tool-parts.ts`. Unknown kinds should fall back to `GenericTool`.

### Markdown

- **Streamdown** (`ui/streamdown.tsx`) is the only markdown renderer in feature code.
- Do not add alternate markdown renderers.

### Reuse guidelines

1. **Chat/message/tool UI** → extend or install from `@agent-elements` only (adapter pipeline + `ToolRenderer`).
2. **Shared non-chat patterns** → `components/product/*` (never reasoning/tool transcript cards).
3. **Primitives** → `components/ui/*` via `npx shadcn@latest add …`.
4. **Extend before install** — add props/variants before pulling a new registry component.
5. **Canonical `cn()`** → `@/lib/utils` only (not `agent-elements/utils/cn.ts`).

### External documentation

- **Agent Elements**: https://agent-elements.21st.dev/docs — full catalog: https://agent-elements.21st.dev/llms-full.txt
- **shadcn/ui**: https://ui.shadcn.com/docs — copy-paste components; namespaced registries in CLI 3.x
- **Base UI**: https://base-ui.com/react/overview
- **TanStack Router**: https://tanstack.com/router/latest/docs/framework/react/overview
- **TanStack Query**: https://tanstack.com/query/latest/docs/framework/react/overview
- **Vercel AI SDK** (`UIMessage` types): https://ai-sdk.dev/docs — types only; transport is fleet websocket adapters

## Agent Notes

- `components.json` defines the `@/*` alias and the shadcn/Base UI style baseline.
- Keep runtime labels, route behavior, and endpoint expectations aligned with the backend contract.
- All feature logic lives in `src/features/*`, `src/lib/*`, or `src/components/product/*`.
- Tool transcript UI belongs under `components/agent-elements/tools/*`.
- Optimization is a supported product surface at `/app/optimization`; History remains API-only until v1.1.
- Do not recreate a screen-layer `workspace-adapter.ts`; adapter logic belongs in `src/lib/workspace/`.
- The Volumes provider switcher is **page-scoped** and must not become a global runtime setting.
- `code-block` lives in `ui/code-block.tsx` — both the simple (`CodeBlock`/`CodeBlockCode`/`CodeBlockGroup`) and rich (`CodeBlockViewer`/`CodeBlockHeader`/`CodeBlockFilename`/`CodeBlockCopyButton`/`CodeBlockActions`/`CodeBlockContent`) APIs.
- `trajectory-chain.tsx` uses `agent-elements` `Collapsible` primitives directly — do not reintroduce `chain-of-thought` from `@ai-elements`.
- The orphaned `workspace-composer.tsx` and its legacy `PromptInput` dependency have been deleted; do not recreate them.
- Empty-state suggestions in `workspace-message-list.tsx` use the Agent Elements `Suggestion` chip
  component with neutral icon fills and the larger "Qredence Fleet" title; avoid card-like empty
  states or colored icon accents.
