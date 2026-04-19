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
4. Preserve supported surfaces: **Workbench**, **Volumes**, **Optimization**, **Settings**, **History**.
5. Keep retired paths (`taxonomy`, `skills`, `memory`, `analytics`) falling through to `/404`.

---

## Source-of-Truth Files

| Concern                 | File(s)                                                            |
| ----------------------- | ------------------------------------------------------------------ |
| Scripts & validation    | `package.json`                                                     |
| Lint/build/import rules | `vite.config.ts`                                                   |
| Routes & surfaces       | `src/routes/*`                                                     |
| App chrome / layout     | `src/features/layout/*`                                            |
| Product surfaces        | `src/features/{workspace,volumes,settings,optimization,history}/*` |
| UI primitives           | `src/components/ui/*` (shadcn/Base UI)                             |
| AI Elements             | `src/components/ai-elements/*`                                     |
| Product compositions    | `src/components/product/*`                                         |
| API clients & types     | `src/lib/rlm-api/*`                                                |
| Workspace adapters      | `src/lib/workspace/*`                                              |
| Theme / tokens          | `src/styles/globals.css`                                           |
| shadcn config           | `components.json`                                                  |
| API contract            | `openapi.yaml`, `src/lib/rlm-api/generated/openapi.ts`             |

### Generated / Synced — Do Not Hand-Edit

- `src/routeTree.gen.ts`
- `src/lib/rlm-api/generated/openapi.ts`
- `openapi/fleet-rlm.openapi.yaml`
- `dist/`

---

## Architecture

### Component Layers (outer → inner)

1. **`src/components/ui/*`** — shadcn/Base UI primitives. Thin, semantic, no feature/runtime imports.
2. **`src/components/ai-elements/*`** — AI Elements registry. Composable, registry-aligned.
3. **`src/components/product/*`** — Reusable product compositions (empty states, skeletons, panels).
4. **`src/features/layout/*`** — App chrome. Consumes workspace/volumes through feature entrypoints only.
5. **`src/features/{workspace,volumes,settings,optimization,history}/*`** — Canonical surface ownership.
6. **`src/lib/{rlm-api,workspace}/*`** — API clients, adapters, stores, frame shaping.
7. **`src/stores/*`** — Cross-app shell/layout and navigation state.

### Import Boundaries (enforced in `vite.config.ts`)

- `src/components/{ui,ai-elements,product}/*` **must not** import from `src/screens/*`.
- `src/lib/workspace/*` **must not** depend on workspace UI modules.
- `src/features/layout/*` **must** consume workspace/volumes through their feature entrypoints or explicit public contracts.
- `@/lib/utils` is the canonical `cn()` import path.

### Route Ownership

- `src/router.tsx` owns the router instance.
- `src/routes/` defines file-based routes. Keep route wrappers thin; compose feature entry modules (e.g., `screen/*`).
- `src/routeTree.gen.ts` is generated.

### Workspace Structure

Responsibility folders under `src/features/workspace/`:

- `screen/` — route entry
- `conversation/` — chat rendering
- `composer/` — input / prompt UI
- `inspection/` — detail panels
- `workbench/` — execution trace / workbench
- `session/` — session management

Assistant transcript/content modeling belongs under:
`src/features/workspace/conversation/assistant-content/model/`

**Do not** create feature-local `ui/` folders; `src/components/ui/*` is the only primitive `ui` namespace.

---

## Tech Stack

- **Package manager:** `pnpm` (always `pnpm install --frozen-lockfile`)
- **Build / lint / format:** Vite+ (`vp`) via `pnpm run ...`
- **Framework:** React 19 + TypeScript 5.9+
- **Router:** TanStack Router (file-based)
- **State:** Zustand + TanStack Query
- **Styling:** Tailwind CSS v4 + `tw-animate-css` + `@theme inline`
- **Testing:** Vitest (unit), Playwright (e2e)

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
pnpm run lint:robustness     # alias
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

# Full validation
pnpm run check               # type-check + lint + test:unit + build + test:e2e
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

- Theme primitives live in `src/styles/globals.css`. Keep the Tailwind v4 baseline canonical.
- Use **semantic tokens and shared variants** — avoid arbitrary colors or local token layers.
- **Eliminate arbitrary Tailwind values**. The project maintains token-backed `@utility` classes for all font sizes. Do not introduce new `text-[Npx]`, `w-[Npx]`, `h-[Npx]`, `rounded-[Npx]`, `leading-[...]`, or `tracking-[...]` values. If a size is missing, add a design token and `@utility` in `globals.css` rather than using an arbitrary value.
- **Typography utilities** (use these instead of arbitrary font sizes):
  - `typo-micro` — 8px (`text-3xs`)
  - `typo-helper` — 10px (`text-2xs`)
  - `typo-body-xs` — 11px
  - `typo-caption` — 12px (`text-xs`)
  - `typo-body-sm` — 13px
  - `typo-label` / `typo-label-regular` — 14px (`text-sm`)
  - `typo-base` — 14px (base body)
  - `typo-display` — 32px (`text-[2rem]`)
  - `tracking-tight-custom` — `-0.18px` (sidebar, composer)
  - `tracking-tighter-custom` — `-0.05em` (display headings)
  - `tracking-wide-custom` — `0.12em` (uppercase labels)
  - `tracking-wider-custom` — `0.08em` (uppercase mono labels)
  - `leading-loose-custom` — `1.7142857` (file preview line numbers)
- **Layout width utilities**:
  - `max-w-4/5` — `80%`
  - `max-w-message` — `95%`
  - `max-w-skeleton` — `280px`
  - `max-w-drawer-sm` — `200px`
  - `max-w-drawer-xs` — `180px`
  - `w-select-xl` — `132px`
- **Runtime-driven styles** (e.g. dynamic colors from `STEP_TYPE_META`) must use CSS custom properties set via the `style` prop, consumed by `@utility` classes in `globals.css`. Do not use inline `style={{ color: ..., backgroundColor: ... }}` for repeated patterns. Example:
  ```tsx
  <div style={{ "--node-color": meta.color } as React.CSSProperties} className="node-color-text node-tint">
  ```
- **Shared visual recipes** belong in `src/components/product/*`, not duplicated locally. Current product components:
  - `NodeBadge` — small badge/pill for graph nodes and execution metadata
- Preserve shell/layout root stacking context for portaled overlays.

## React & Runtime Rules

- Prefer **React 19 direct ref passing** over `forwardRef` by default.
- `daytona_pilot` is the public runtime label. Request controls: `execution_mode`, `repo_url`, `repo_ref`, `context_paths`, `batch_concurrency`.
- Runtime labels shown to users should describe the Daytona-backed workbench path only.
- Shared runtime status queries: `src/hooks/use-runtime-status.ts`.
- The **Volumes** surface represents mounted durable storage, not the transient live workspace.

## Naming Conventions

- New handwritten feature files: `kebab-case`
- React components: `PascalCase`
- Hooks: `useThing`
- Framework exceptions preserved: `App.tsx`, `__root.tsx`, `$.tsx`

## Testing Conventions

- Colocate tests under `__tests__/` when practical.
- Tests for `src/lib/workspace/*` and `src/features/workspace/{conversation,composer,inspection,screen,session,workbench}/*` should import owners directly, not via route wrappers or compatibility barrels.

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
VITE_AGENTATION_ENDPOINT
VITE_ENTRA_CLIENT_ID
VITE_ENTRA_SCOPES
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

## AI Component Registries

The project uses **prompt-kit** as the primary AI component registry and **AI SDK Elements** as the secondary registry for AI-SDK-native features (e.g., `attachments`). Both are configured in `components.json`.

### Registry Installation

```bash
# prompt-kit (installs into src/components/ai-elements/ by default)
npx shadcn@latest add "https://prompt-kit.com/c/{component}.json" -p src/components/ai-elements

# AI SDK Elements
npx shadcn@latest add "https://ai-sdk.dev/elements/api/registry/{component}.json" -p src/components/ai-elements
```

**Important**: Always use `-p src/components/ai-elements` to avoid overwriting UI primitives in `src/components/ui/`. If the CLI installs bundled UI primitives (button, hover-card, etc.) into `ai-elements/`, delete them — the project canonical versions live in `src/components/ui/`.

### Component Catalog & Reuse Policy

**Before installing any new component, check this catalog.** The project already has multiple components solving the same problem. Prefer reuse over proliferation.

#### Canonical AI Components (actively used — reuse these)

| Component | Location | Consumers | Purpose |
|-----------|----------|-----------|---------|
| `Message` | `ai-elements/message.tsx` | workspace chat (5 files) | Chat message shell with Streamdown rendering, branch support, actions |
| `Conversation` | `ai-elements/conversation.tsx` | workspace-message-list, chat-empty-state | StickToBottom scroll container with empty state & download |
| `Reasoning` | `ai-elements/reasoning.tsx` | 4 workspace files | Collapsible thinking block with duration tracking |
| `ChainOfThought` | `ai-elements/chain-of-thought.tsx` | execution-inspector-tab | Step-by-step reasoning timeline |
| `Tool` | `ai-elements/tool.tsx` | render-primitives, trace-part-renderers | Tool call/result display with status badges |
| `Sources` | `ai-elements/sources.tsx` | trace-part-renderers | Collapsible source list (Book icon + count) |
| `Suggestion` | `ai-elements/suggestion.tsx` | workspace-chat-empty-state | Scrollable suggestion pills with onClick handler |
| `PromptInput` | `ai-elements/prompt-input/` | workspace-composer | Full composer input (textarea + attachments + send) |
| `InlineCitation` | `ai-elements/inline-citation.tsx` | trace-part-renderers | Inline numbered citation badges |
| `Task` | `ai-elements/task.tsx` | trace-part-renderers | Task status display component |
| `Shimmer` | `product/text-shimmer.tsx` | reasoning, answer-block, trace-renderers | Animated text shimmer (loading state) |
| `Streamdown` | `ui/streamdown.tsx` | 5 workspace files | Canonical markdown renderer (streaming-safe) |

#### Removed Registry Components

The following prompt-kit / AI SDK Elements components were previously installed but had zero consumers and were removed during consolidation. Do not reinstall them:

| Component | Was At | Replacement |
|-----------|--------|-------------|
| `Markdown` | `ai-elements/markdown.tsx` | `Streamdown` is canonical |
| `Message` (prompt-kit) | `ui/message.tsx` | Hand-rolled `ai-elements/message.tsx` |
| `ChatContainer` | `ai-elements/chat-container.tsx` | `Conversation` |
| `ScrollButton` | `ai-elements/scroll-button.tsx` | `ConversationScrollButton` |
| `PromptSuggestion` | `ai-elements/prompt-suggestion.tsx` | `Suggestion` |
| `Source` (prompt-kit) | `ai-elements/source.tsx` | `Sources` |
| `ResponseStream` | `ai-elements/response-stream.tsx` | N/A — no use case |
| `Attachments` | `ai-elements/attachments.tsx` | N/A — no use case |

#### Markdown Renderers — One Canonical Choice

- **Streamdown** (`ui/streamdown.tsx`) is the **only** markdown renderer used in feature code. It wraps the `streamdown` package with streaming-safe parsing, CJK/code/math/mermaid plugins, and Tailwind typography.
- `ui/markdown.tsx` (react-markdown wrapper) exists in the primitives layer but has **no feature consumers**. Do not introduce new markdown renderers.

### Reuse Guidelines

1. **Check existing components first.** The hand-rolled AI components are mature, styled for the product, and have active consumers. Do not install a registry component that duplicates `Message`, `Conversation`, `Reasoning`, `Tool`, `Sources`, `Suggestion`, or `PromptInput` without a clear capability gap.
2. **Registry components are for net-new capabilities.** Only install from prompt-kit or AI SDK Elements when the project genuinely lacks a component for the use case (e.g., a new `thread-list` or `command` component).
3. **If you install a registry component, migrate to it.** Do not leave registry components as dead code. Update consumers or remove the installed file if adoption doesn't happen within the same PR.
4. **Do not hand-roll new message/reasoning/tool/suggestion/citation components.** Extend the existing ones or install from registries.
5. **Extend, don't duplicate.** If a component is close to what you need, add props or variants rather than creating a second component. Example: `Suggestion` already supports `wrap` and `onClick(suggestion)` — extend it before installing `PromptSuggestion`.

### External Documentation References

- **shadcn/ui**: https://ui.shadcn.com/docs — Open-code component distribution platform. Not a library; components are copied into `src/components/ui/` and owned by the project.
- **Base UI**: https://base-ui.com/react/overview — Headless, accessible primitives (Accordion, Dialog, Select, etc.). Used as the underlying layer for many shadcn/ui components.
- **TanStack Router**: https://tanstack.com/router/latest/docs/framework/react/overview — File-based routing with 100% inferred TypeScript, search-param state management, and built-in caching.
- **TanStack Query**: https://tanstack.com/query/latest/docs/framework/react/overview — Server-state management (fetching, caching, synchronization). Used via `src/hooks/use-*` and feature data layers.
- **prompt-kit**: https://www.prompt-kit.com/docs — AI interface components built on shadcn/ui. Registry URL: `https://www.prompt-kit.com/c/{name}.json`.
- **AI SDK Elements**: https://elements.ai-sdk.dev/docs — AI-native components (messages, attachments, etc.) built for the Vercel AI SDK. Registry URL: `https://ai-sdk.dev/elements/api/registry/{name}.json`.

## Agent Notes

- `components.json` defines the `@/*` alias and the shadcn/Base UI style baseline.
- Keep runtime labels, route behavior, and endpoint expectations aligned with the backend contract.
- `src/screens/*` no longer exists. All feature logic lives in `src/features/*`, `src/lib/*`, or `src/components/product/*`.
- `History` is a supported surface at `/app/history`.
- Do not recreate a screen-layer `workspace-adapter.ts`; adapter logic belongs in `src/lib/workspace/`.
- The Volumes provider switcher is **page-scoped** and must not become a global runtime setting.
- Settings should consume the shared optimization form from `features/optimization/optimization-form`.
