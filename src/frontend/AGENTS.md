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

| Concern                 | File(s)                                                   |
| ----------------------- | --------------------------------------------------------- |
| Scripts & validation    | `package.json`                                            |
| Lint/build/import rules | `vite.config.ts`                                          |
| Routes & surfaces       | `src/routes/*`                                            |
| App chrome / layout     | `src/features/layout/*`                                   |
| Product surfaces        | `src/features/{workspace,optimization,volumes,settings}/*` |
| UI primitives           | `src/components/ui/*` (shadcn/Base UI)                    |
| Agent Elements (chat)   | `src/components/agent-elements/*`                         |
| Legacy inspection UI    | `src/components/ai-elements/*` (composer/inspection only) |
| Product compositions    | `src/components/product/*`                                |
| API clients & types     | `src/lib/rlm-api/*`                                       |
| Workspace adapters      | `src/lib/workspace/*`                                     |
| Theme / tokens          | `src/styles/globals.css`                                  |
| shadcn config           | `components.json`                                         |
| API contract            | `openapi.yaml`, `src/lib/rlm-api/generated/openapi.ts`    |

### Generated / Synced — Do Not Hand-Edit

- `src/routeTree.gen.ts`
- `src/lib/rlm-api/generated/openapi.ts`
- `openapi/fleet-rlm.openapi.yaml`
- `dist/`

---

## Architecture

### Component Layers (outer → inner)

1. **`src/components/ui/*`** — shadcn/Base UI primitives. Thin, semantic, no feature/runtime imports.
2. **`src/components/agent-elements/*`** — **Canonical agent/chat UI** ([Agent Elements](https://agent-elements.21st.dev/docs) shadcn registry). `AgentChat`, `InputBar`, tool cards, `UIMessage`-shaped transcripts.
3. **`src/components/product/*`** — Reusable product compositions (empty states, skeletons, panels). Do not add chat, reasoning, or tool transcript UI here; use Agent Elements.
4. **`src/components/ai-elements/*`** — **Legacy inspection/composer primitives only** (`prompt-input`, `chain-of-thought`). Do not add new chat/message/tool components here.
5. **`src/features/layout/*`** — App chrome. Consumes workspace/volumes through feature entrypoints only.
6. **`src/features/{workspace,optimization,volumes,settings}/*`** — Canonical surface ownership.
7. **`src/lib/{rlm-api,workspace}/*`** — API clients, adapters, stores, frame shaping.
8. **`src/stores/*`** — Cross-app shell/layout and navigation state.

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

- `src/components/{ui,ai-elements,agent-elements,product}/*` **must not** import from `src/screens/*`.
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

- **Package manager:** `pnpm` 10.33.0 (always `pnpm install --frozen-lockfile`)
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
pnpm run lint:robustness     # alias for lint
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
VITE_FLEET_WORKSPACE_ID
VITE_FLEET_USER_ID
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

## Agent Elements and shadcn Registries

[Agent Elements](https://agent-elements.21st.dev/docs) is the **canonical chat/agent UI kit**. Official guidance: **do not mix** Agent Elements with `ai-elements`, CopilotKit, or other chat kits for message/tool surfaces.

Registries are configured in [components.json](components.json):

| Namespace         | URL                                                    | Use                                |
| ----------------- | ------------------------------------------------------ | ---------------------------------- |
| `@agent-elements` | `https://agent-elements.21st.dev/r/{name}.json`        | Chat shell, tool cards, input bar  |
| `@ai-elements`    | `https://ai-sdk.dev/elements/api/registry/{name}.json` | Do not install new chat components |
| `@prompt-kit`     | `https://www.prompt-kit.com/c/{name}.json`             | Avoid unless net-new capability    |

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

| Component           | Location                                    | Consumers                                               | Purpose                                              |
| ------------------- | ------------------------------------------- | ------------------------------------------------------- | ---------------------------------------------------- |
| `AgentChat`         | `agent-elements/agent-chat.tsx`             | `workspace-message-list.tsx`                            | Full chat shell (messages + input)                   |
| `InputBar`          | `agent-elements/input-bar.tsx`              | `workspace-agent-input-bar.tsx`                         | Composer with mode/model pickers                     |
| `Suggestions`       | `agent-elements/input/suggestions.tsx`      | `AgentChat` via `workspace-message-list.tsx`            | Quick-prompt chips (empty state + input)             |
| Tool mapping        | `lib/workspace/agent-tool-parts.ts`         | `agent-chat-adapter.ts`, `execution-inspector-rows.tsx` | Shared tool part normalization                       |
| Static tool helpers | `agent-elements/utils/static-tool-parts.ts` | workbench, inspector                                    | `ThinkingTool` steps outside chat transcripts        |
| Tool cards          | `agent-elements/tools/*`                    | `agent-chat-adapter.ts` via `toolRenderers`             | Bash, Edit, Search, MCP, Subagent, Thinking, Generic |
| `TextShimmer`       | `agent-elements/text-shimmer.tsx`           | tool rows, loading states                               | Streaming label shimmer                              |
| `Streamdown`        | `ui/streamdown.tsx`                         | agent-elements markdown                                 | Canonical markdown renderer                          |

Wire backend tools through `agent-chat-adapter.ts` → `ToolRenderer`. Shared normalization lives in `lib/workspace/agent-tool-parts.ts`. Unknown kinds should fall back to `GenericTool`.

### Legacy `ai-elements/` (composer only)

| Component        | Location                           | Consumers                | Notes                                                |
| ---------------- | ---------------------------------- | ------------------------ | ---------------------------------------------------- |
| `PromptInput`    | `ai-elements/prompt-input/`        | `workspace-composer.tsx` | Legacy composer; prefer `InputBar` for new work      |
| `ChainOfThought` | `ai-elements/chain-of-thought.tsx` | —                        | Removed from execution inspector; do not reintroduce |
| `Reasoning`      | `ai-elements/reasoning.tsx`        | tests only               | Prefer `ThinkingTool` via `static-tool-parts.ts`     |

Do **not** install `Message`, `Conversation`, `Tool`, or other chat primitives from `@ai-elements` or `@prompt-kit`.

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
- `src/screens/*` no longer exists. All feature logic lives in `src/features/*`, `src/lib/*`, or `src/components/product/*`.
- Optimization is a supported product surface at `/app/optimization`; History remains API-only until v1.1.
- Do not recreate a screen-layer `workspace-adapter.ts`; adapter logic belongs in `src/lib/workspace/`.
- The Volumes provider switcher is **page-scoped** and must not become a global runtime setting.
