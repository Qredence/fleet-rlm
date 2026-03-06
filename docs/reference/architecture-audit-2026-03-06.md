# Architecture Audit (2026-03-06)

## Status Note

- This document is the 2026-03-06 baseline snapshot, not the live execution checklist.
- The active implementation state now lives in `PLANS.md` and `TASKS.md`.
- The `RLM Workspace` and `Volumes` cleanup has since moved the live frontend ownership path away from several older `app/pages/skill-creation/*` and `features/taxonomy/*` references captured below.
- Current live ownership is `app/pages/RlmWorkspacePage.tsx` → `features/rlm-workspace/*` and `app/pages/VolumesPage.tsx` → `features/volumes/*`; older `skill-creation` and `taxonomy` path mentions below are historical references, not live ownership roots.

This audit maps the current `fleet-rlm` backend, the `src/frontend` Vite app, and the active test surface. It also records the highest-value cleanup opportunities for making the codebase cleaner, more maintainable, and more honest about which features are truly wired end to end.

## Scope

- Frontend architecture in `src/frontend/src`
- FastAPI and DSPy runtime architecture in `src/fleet_rlm`
- Tests under `tests/` and colocated frontend tests
- Cleanup opportunities for compatibility shims, mock fallbacks, and stale contract seams

## Executive Summary

The codebase already has a strong core around the WebSocket-first runtime path:

- The backend is centered on a clear FastAPI app factory in `src/fleet_rlm/server/main.py`.
- The real product flow is the chat/runtime path backed by `src/fleet_rlm/server/routers/ws/` and `src/fleet_rlm/react/`.
- The frontend already uses modern primitives correctly in several places: React Router v7, TanStack Query, Zustand, shadcn/Radix components, Tailwind v4, and a typed backend layer in `src/frontend/src/lib/rlm-api/*`.

The biggest cleanliness problems are not missing libraries. They are architectural honesty and boundary discipline:

1. Several frontend sections still present as product surfaces while degrading to mock or fallback data because the corresponding FastAPI endpoints were intentionally removed.
2. The frontend mixes true backend-driven runtime flows with mock/demo flows in the same app shell, which increases maintenance and obscures what is actually supported.
3. The backend DSPy surface is strong on signatures and runtime modules, but still carries compatibility seams and a few overly broad modules that should be reduced rather than extended.
4. The test suite is mostly aligned with the current codebase; the main risk is not “too many obsolete tests”, but tests that preserve compatibility layers longer than the product should.

Implementation note:

- The active cleanup program now uses `RLM Workspace` as the product name for the chat/runtime surface and `Volumes` for the runtime-backed file browser. Internal `skill-creation` and `taxonomy` module names may still exist temporarily while the live tree is being normalized.
- The active cleanup program now uses `RLM Workspace` as the product name for the chat/runtime surface and `Volumes` for the runtime-backed file browser. Older `skill-creation` and `taxonomy` references in this document should be read as historical context only.

## Current Code Map

### Frontend

#### Boot and providers

- Entry point: `src/frontend/src/main.tsx`
- Router root: `src/frontend/src/app/App.tsx`
- Route table: `src/frontend/src/app/routes.ts`
- Top-level providers: `src/frontend/src/app/providers/AppProviders.tsx`
- Query provider: `src/frontend/src/app/providers/QueryProvider.tsx`
- App shell/layout: `src/frontend/src/app/layout/RootLayout.tsx`

#### Primary frontend domains

- Backend transport and contract typing: `src/frontend/src/lib/rlm-api/*`
- Runtime chat flow: `src/frontend/src/app/pages/RlmWorkspacePage.tsx` → `src/frontend/src/features/rlm-workspace/*`
- Volumes browser flow: `src/frontend/src/app/pages/VolumesPage.tsx` → `src/frontend/src/features/volumes/*`
- Artifact execution canvas: `src/frontend/src/features/artifacts/*`
- Settings/runtime controls: `src/frontend/src/features/settings/*`
- Shared shadcn/Radix UI primitives: `src/frontend/src/components/ui/*`
- Shared app components: `src/frontend/src/components/shared/*`
- Cross-cutting hooks: `src/frontend/src/hooks/useAuth.ts`, `useFilesystem.ts`, `useNavigation.ts`
- Local state stores: `src/frontend/src/screens/chat/stores/chatStore.ts`, `src/frontend/src/stores/artifactStore.ts`, `src/frontend/src/stores/mockStateStore.ts`

#### Styling

- Tailwind v4 entry: `src/frontend/src/styles/tailwind.css`
- Token/theme layer: `src/frontend/src/styles/theme.css`
- App-wide styles: `src/frontend/src/styles/index.css`
- Fonts: `src/frontend/src/styles/fonts.css`

### Backend

#### FastAPI app and server runtime

- App factory and lifespan: `src/fleet_rlm/server/main.py`
- Config: `src/fleet_rlm/server/config.py`
- DI/state: `src/fleet_rlm/server/deps.py`
- Middleware: `src/fleet_rlm/server/middleware.py`
- HTTP routers: `src/fleet_rlm/server/routers/auth.py`, `health.py`, `runtime.py`, `sessions.py`
- WS runtime: `src/fleet_rlm/server/routers/ws/`

#### DSPy and orchestration

- High-level runners: `src/fleet_rlm/runners.py`
- ReAct agent: `src/fleet_rlm/react/agent.py`
- Signatures: `src/fleet_rlm/react/signatures.py`
- Cached RLM modules: `src/fleet_rlm/react/rlm_runtime_modules.py`
- Runtime factory: `src/fleet_rlm/react/runtime_factory.py`
- Tool assembly: `src/fleet_rlm/react/tools/__init__.py`
- Delegate/memory runtime tools: `src/fleet_rlm/react/tools/delegate.py`, `memory_intelligence.py`
- Interpreter: `src/fleet_rlm/core/interpreter.py`

#### Persistence and analytics

- Repository and DB models: `src/fleet_rlm/db/repository.py`, `models.py`, `engine.py`
- Analytics: `src/fleet_rlm/analytics/*`

### Tests

#### Backend and contract tests

- Server contract tests: `tests/ui/server/test_api_contract_routes.py`
- WS behavior tests: `tests/ui/ws/*`
- React/DSPy unit tests: `tests/unit/test_react_agent.py`, `test_react_streaming.py`, `test_react_tools.py`, `test_rlm_state.py`
- Import guard rails: `tests/unit/test_canonical_imports.py`, `test_removed_legacy_paths.py`

#### Frontend tests

- API contract/runtime tests: `src/frontend/src/lib/rlm-api/__tests__/*`
- Store tests: `src/frontend/src/screens/chat/stores/__tests__/*`
- Artifact tests: `src/frontend/src/features/artifacts/components/__tests__/*`
- UI smoke E2E: `src/frontend/tests/e2e/*`

## Findings

### 1. Frontend feature truth and backend truth are now aligned, but historical references still lag

The active product surfaces now match the backend contract: the live app routes users to `RLM Workspace`, `Volumes`, and `Settings`, while legacy `skills`, `memory`, `analytics`, and `taxonomy` URLs redirect to canonical supported routes.

Evidence:

- `src/frontend/src/app/routes.ts` only exposes `workspace`, `volumes`, and `settings` as first-class `/app/*` children.
- `src/frontend/src/lib/perf/routePreload.tsx` preloads `RlmWorkspacePage`, `VolumesPage`, and `SettingsPage`.
- `src/frontend/AGENTS.md` now documents `workspace`, `volumes`, and `settings` as the only supported product surfaces.
- The remaining `skill-creation` and `taxonomy` directories in the tree are test-only or empty transitional shells, not live route ownership roots.

Why this still matters:

- Historical docs and generated reference artifacts still mention deleted pages, removed hooks, and pre-cleanup ownership paths.
- Those stale references make the tree look more fragmented than the live app actually is.

Recommendation:

1. Keep the canonical route/domain mapping explicit in docs: thin `app/pages/*` entrypoints, real ownership under `features/rlm-workspace/*` and `features/volumes/*`.
2. Treat legacy-named directories as cleanup debt, not supported surfaces.
3. Regenerate historical reference artifacts after the checkpoint commit when broader cleanup work resumes.

### 2. Frontend state ownership is mostly good, with only transitional leftovers remaining

The frontend already uses the right tools:

- TanStack Query for server-state hooks.
- Zustand for streaming/local interaction state.

What is not yet clean is the boundary between real runtime state and demo/mock state.

Evidence:

- Real runtime chat state lives in `src/frontend/src/features/chat/stores/chatStore.ts`.
- The canonical runtime flow is `src/frontend/src/features/rlm-workspace/useBackendChatRuntime.ts`.
- The old `skill-creation` runtime ownership path is no longer active; only test-only `app/pages/skill-creation/__tests__/` remains, and `src/frontend/src/lib/skill-creation/simulation/` is currently empty.

Why this matters:

- The runtime boundary is much clearer than it was in the original baseline, but the leftover directory names can still imply a second operating mode that no longer exists in the live product path.

Recommendation:

1. Keep the backend-driven `RLM Workspace` flow as the only live runtime path.
2. Remove empty simulation/taxonomy shells in a dedicated cleanup pass once the branch is checkpointed.
3. Keep Zustand limited to ephemeral client state:
   - streaming session state
   - artifact canvas/UI state
   - mock/demo-only state if retained
4. Keep TanStack Query limited to backend-backed resources.

### 3. The frontend structure is close, and the remaining work is mostly transitional cleanup

The current structure is better than a flat Vite app, but it still has overlapping ownership lines between `app`, `features`, `hooks`, `components`, and `stores`.

Examples:

- Route ownership is now thin and explicit in `app/pages/RlmWorkspacePage.tsx` and `app/pages/VolumesPage.tsx`.
- Domain ownership is correctly centered under `features/rlm-workspace/*` and `features/volumes/*`.
- The remaining confusing pieces are legacy-named directories that are now empty or test-only rather than active code paths.

Recommendation:

1. Continue standardizing on:
   - `app/` for boot, routing, providers, shell layout
   - `features/<domain>/` for domain UI, adapters, state, and tests
   - `lib/rlm-api/` for transport and shared backend contract utilities
   - `components/ui/` only for reusable primitives
2. Keep route files thin and domain composition-heavy.
3. Delete or archive the remaining empty/test-only legacy directories so the filesystem matches the current architecture story.

### 4. shadcn and Tailwind are already present; the real opportunity is consistency

The project is already on the correct stack:

- shadcn config exists in `src/frontend/components.json`
- Tailwind v4 entry exists in `src/frontend/src/styles/tailwind.css`
- Design tokens are centralized in `src/frontend/src/styles/theme.css`

The main issues are:

- A very large custom theme surface with many product-specific tokens.
- A broad `components/ui/*` surface that mixes upstream-style primitives with highly customized components.
- Some domain components still carry visual logic that could be normalized around shared patterns.

Recommendation:

1. Keep `components/ui/*` reserved for true primitives and primitive wrappers.
2. Move domain-specific composed widgets out of `components/ui/*` when they encode product behavior rather than reusable primitive behavior.
3. Keep theme tokens, but group them into:
   - semantic core tokens
   - shell/layout tokens
   - specialty visual tokens
4. Avoid adding more custom UI primitives until existing ones are classified as:
   - primitive
   - composed shared component
   - domain component

### 5. Accessibility posture is decent, but should be validated through the shell, not assumed

There are positive signs:

- Radix/shadcn primitives are widely used.
- Mobile navigation accounts for touch targets via `touch-target` utility in `tailwind.css`.
- `sidebar.tsx` includes screen-reader-only `SheetHeader` content for mobile.
- `RootLayout` uses a consistent toaster placement strategy.

Risks remain:

- The app has a lot of custom interaction surfaces layered on top of Radix primitives.
- Unsupported/degraded sections can create confusing affordances if they look interactive but are not genuinely available.
- The shell is responsive and stateful, so keyboard/focus regressions are likely to appear there first.

Recommendation:

1. Add a targeted browser accessibility pass over:
   - navigation shell
   - command palette
   - skill creation chat input
   - artifact canvas switching
   - settings dialogs
2. Add focused regression tests for:
   - keyboard navigation across the shell
   - disabled/degraded section announcements
   - dialog focus trapping and dismissal

### 6. FastAPI structure is strong, but the next step is decomposition, not expansion

The backend is already following many of the right FastAPI practices:

- Router prefixes/tags are defined at router construction time.
- Response models are declared on HTTP routes.
- `Annotated` dependency aliases are used in `src/fleet_rlm/server/deps.py`.
- Blocking work in `runtime.py` uses `asyncio.to_thread(...)` rather than running synchronously inside `async def`.

The main problem is complexity concentration in a few modules:

- `src/fleet_rlm/server/routers/ws/api.py`
- `src/fleet_rlm/server/execution/step_builder.py`
- `src/fleet_rlm/react/agent.py`
- `src/fleet_rlm/runners.py`

Recommendation:

1. Keep the public FastAPI surface stable.
2. Continue splitting orchestration-heavy modules by responsibility:
   - request/auth/session bootstrap
   - runtime assembly
   - streaming turn orchestration
   - persistence lifecycle
3. Add typed aliases for more non-request dependencies where useful.
4. Upgrade middleware typing in `src/fleet_rlm/server/middleware.py` so request/response/call-next signatures are explicit and easier to evolve.

### 7. DSPy usage is a strength of the backend, but it should be made more canonical

The DSPy side is not weak. In fact, it is one of the cleaner parts of the codebase:

- Signature definitions are explicit and centralized in `src/fleet_rlm/react/signatures.py`.
- Reusable cached RLM wrappers exist in `src/fleet_rlm/react/rlm_runtime_modules.py`.
- The ReAct agent is a real `dspy.Module` in `src/fleet_rlm/react/agent.py`.
- The backend correctly differentiates planner LM and delegate LM paths.

Where cleanup is still needed:

- Runtime wrappers in `rlm_runtime_modules.py` are repetitive and could be declared from a registry/factory table.
- Compatibility wrappers still exist around sync/async tool invocation in `tool_delegation.py`.
- Some “canonical” DSPy usage is distributed between `runners.py`, `react/agent.py`, and tool modules rather than described through a smaller number of runtime composition patterns.

Recommendation:

1. Introduce a registry for runtime module definitions:
   - runtime module name
   - signature
   - human purpose
   - default runtime limits
2. Use that registry to reduce repetitive class boilerplate where possible.
3. Keep direct `dspy.RLM` creation inside clearly named factories or modules, not ad hoc callers.
4. Remove compatibility-only tool wrapping once the async/sync contract is settled.

### 8. Compatibility shims should keep shrinking

Two kinds of shim behavior remain:

- frontend data barrels and fallback layers
- backend compatibility imports/wrappers

This audit removed one frontend shim:

- deleted `src/frontend/src/lib/data/mock-skills.ts`
- deleted `src/frontend/src/lib/data/mock/index.ts`

Remaining shim-like surfaces worth reviewing:

- `src/fleet_rlm/utils/tools.py`
- comments in `src/fleet_rlm/server/routers/ws/__init__.py` that imply one-release compatibility intent
- frontend “capability fallback” hooks for removed backend domains

Recommendation:

1. Prefer direct canonical imports.
2. Keep compatibility shims only for public package boundaries that still need a deprecation window.
3. Delete private/internal compatibility layers once in-repo usage reaches zero.

### 9. Test cleanup should be conservative

The current test suite is more aligned than stale.

Tests that still look justified:

- `tests/unit/test_removed_legacy_paths.py`
- `tests/unit/test_canonical_imports.py`
- `tests/ui/server/test_api_contract_routes.py`
- frontend API/runtime tests under `src/frontend/src/lib/rlm-api/__tests__/`

These protect intentional architectural decisions:

- removed routes stay removed
- canonical imports stay canonical
- WS contract remains the real transport

What should change instead of broad deletion:

1. Delete tests only when the underlying compatibility promise is intentionally dropped.
2. Prefer replacing compatibility-preservation tests with simpler “current contract only” tests once the migration window is over.
3. Add coverage to the real product path rather than inflating tests around degraded/mock-only paths.

## Priority Improvement Plan

### P0

1. Make the frontend navigation honest about supported backend surfaces.
2. Collapse mock/fallback data hooks for domains whose endpoints are intentionally removed.
3. Keep the skill-creation route backend-first; isolate or remove simulation-only mode.

### P1

1. Consolidate frontend domain ownership so route pages stay thin and domain modules own behavior.
2. Split large backend orchestration modules further, especially WS bootstrap and execution-step building.
3. Replace repetitive DSPy runtime wrapper patterns with a registry-driven composition layer.

### P2

1. Reduce remaining compatibility shims and fallback helpers.
2. Reclassify `components/ui/*` into primitives vs composed/shared components.
3. Add targeted accessibility regression coverage over shell, dialogs, and runtime chat.

## Safe Changes Completed In This Audit

1. Removed the frontend mock-data compatibility barrel:
   - deleted `src/frontend/src/lib/data/mock-skills.ts`
   - deleted `src/frontend/src/lib/data/mock/index.ts`
2. Replaced in-repo imports with direct canonical mock-data imports from:
   - `src/frontend/src/lib/data/mock/skills.ts`
   - `src/frontend/src/lib/data/mock/memory.ts`
   - `src/frontend/src/lib/data/mock/filesystem.ts`
3. Removed an unused frontend artifact compatibility subtree that duplicated the live `features/artifacts` implementation:
   - deleted `src/frontend/src/components/domain/artifacts/CanvasSwitcher.tsx`
   - deleted `src/frontend/src/components/domain/artifacts/__tests__/*`
4. Removed an unused backend UI test fixture:
   - deleted `legacy_disabled_client` from `tests/ui/conftest.py`

## Suggested Validation

Frontend:

1. `cd src/frontend && bun run type-check`
2. `cd src/frontend && bun run test:unit`
3. `cd src/frontend && bun run build`

Backend:

1. `uv run pytest -q tests/ui/server/test_api_contract_routes.py tests/unit/test_canonical_imports.py tests/unit/test_removed_legacy_paths.py`
2. `uv run pytest -q tests/unit/test_react_agent.py tests/unit/test_react_streaming.py tests/unit/test_rlm_state.py`
