# Post-Restructure Stabilization & Uncommitted Work Commit Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize the fleet-rlm repository after the Phase 2 backend restructuring (6-boundary modular layout) and the parallel frontend migrations (TanStack Router, prompt-kit, Base UI), by committing all in-flight work as clean, atomic commits and running full validation.

**Architecture:** The backend was restructured from a flat layout into `api/`, `core/`, `infrastructure/`, `features/`, `cli/`, `utils/`. The frontend simultaneously migrated from React Router to TanStack Router, from custom ai-elements to prompt-kit, and from Radix UI primitives to @base-ui/react. There are ~140 uncommitted changed files spanning both subsystems plus new test files, all of which need to be committed as logical units and validated.

**Tech Stack:** Python (uv, ruff, ty, pytest), React 19 + TanStack Router + TanStack Query + Zustand, pnpm, Vite+ (vp), Playwright, OpenAPI codegen.

---

## Pre-Requisites

Before starting, ensure the workspace is set up:

```bash
cd /Volumes/StorageBackup/_RLM/fleet-rlm-dspy
uv sync --all-extras --dev
cd src/frontend && pnpm install --frozen-lockfile && cd ../..
```

---

## Chunk 1: Backend Import Fix-Ups & Lint Cleanup

These changes fix broken cross-module imports left behind after the Phase 2 restructuring moved files into the new `core/agent/`, `core/execution/`, and `features/` boundaries. They also apply ruff formatting fixes.

### Task 1: Fix Stale `core/tools/` → `core/agent/` TYPE_CHECKING Imports

**Files:**
- Modify: `src/fleet_rlm/core/tools/chunking.py` (line ~19: `from ..chat_agent` → `from ..agent.chat_agent`)
- Modify: `src/fleet_rlm/core/tools/delegate.py` (line ~30: same fix)
- Modify: `src/fleet_rlm/core/tools/document.py` (line ~27: same fix)
- Modify: `src/fleet_rlm/core/tools/filesystem.py` (line ~10: same fix)
- Modify: `src/fleet_rlm/core/tools/memory_intelligence.py` (line ~17: same fix)
- Modify: `src/fleet_rlm/core/tools/sandbox.py` (line ~24: same fix)

All six files have the same pattern: a `TYPE_CHECKING` import of `RLMReActChatAgent` that pointed to the old location `..chat_agent` but now needs to be `..agent.chat_agent`.

- [ ] **Step 1: Verify the stale imports exist**

Run:
```bash
grep -rn "from ..chat_agent import" src/fleet_rlm/core/tools/
```
Expected: 6 matches, one per file listed above.

- [ ] **Step 2: Apply the import fix in all six files**

In each of the six files, change:
```python
from ..chat_agent import RLMReActChatAgent
```
to:
```python
from ..agent.chat_agent import RLMReActChatAgent
```

- [ ] **Step 3: Verify no stale references remain**

Run:
```bash
grep -rn "from ..chat_agent import" src/fleet_rlm/core/tools/
```
Expected: 0 matches.

- [ ] **Step 4: Run targeted import check**

Run:
```bash
uv run python -c "from fleet_rlm.core.tools.sandbox import SandboxTools; print('OK')"
```
Expected: `OK` (no ImportError)

- [ ] **Step 5: Commit**

```bash
git add src/fleet_rlm/core/tools/chunking.py \
        src/fleet_rlm/core/tools/delegate.py \
        src/fleet_rlm/core/tools/document.py \
        src/fleet_rlm/core/tools/filesystem.py \
        src/fleet_rlm/core/tools/memory_intelligence.py \
        src/fleet_rlm/core/tools/sandbox.py
git commit -m "fix(core): update TYPE_CHECKING imports in core/tools to point to core/agent"
```

---

### Task 2: Fix `runtime_factory.py` TYPE_CHECKING Import

**Files:**
- Modify: `src/fleet_rlm/core/execution/runtime_factory.py` (line ~14: `from .chat_agent` → `from ..agent.chat_agent`)

- [ ] **Step 1: Verify the stale import**

Run:
```bash
grep -n "from .chat_agent" src/fleet_rlm/core/execution/runtime_factory.py
```
Expected: 1 match at `from .chat_agent import RLMReActChatAgent`

- [ ] **Step 2: Fix the import**

Change:
```python
from .chat_agent import RLMReActChatAgent
```
to:
```python
from ..agent.chat_agent import RLMReActChatAgent
```

- [ ] **Step 3: Verify the import resolves**

Run:
```bash
uv run python -c "from fleet_rlm.core.execution.runtime_factory import get_runtime_module; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/fleet_rlm/core/execution/runtime_factory.py
git commit -m "fix(core): fix runtime_factory TYPE_CHECKING import path after restructure"
```

---

### Task 3: Fix `features/terminal/commands.py` Cross-Boundary Imports

**Files:**
- Modify: `src/fleet_rlm/features/terminal/commands.py` (lines ~105, ~423: `from ..react.commands` → `from ...core.agent.commands`)

- [ ] **Step 1: Verify the stale imports**

Run:
```bash
grep -n "from ..react.commands" src/fleet_rlm/features/terminal/commands.py
```
Expected: 2 matches referencing `COMMAND_DISPATCH`.

- [ ] **Step 2: Fix both imports**

Change both occurrences of:
```python
from ..react.commands import COMMAND_DISPATCH
```
to:
```python
from ...core.agent.commands import COMMAND_DISPATCH
```

- [ ] **Step 3: Verify import resolution**

Run:
```bash
uv run python -c "from fleet_rlm.features.terminal.commands import handle_slash_command; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/fleet_rlm/features/terminal/commands.py
git commit -m "fix(features): update terminal commands import to use core.agent.commands"
```

---

### Task 4: Fix Scaffold Diagnostic Script Import

**Files:**
- Modify: `src/fleet_rlm/features/scaffold/skills/rlm-debug/scripts/diagnose.py` (line ~75: `from fleet_rlm.runners` → `from fleet_rlm.cli.runners`)

- [ ] **Step 1: Verify the stale import**

Run:
```bash
grep -n "from fleet_rlm.runners" src/fleet_rlm/features/scaffold/skills/rlm-debug/scripts/diagnose.py
```
Expected: 1 match.

- [ ] **Step 2: Fix the import**

Change:
```python
from fleet_rlm.runners import check_secret_presence
```
to:
```python
from fleet_rlm.cli.runners import check_secret_presence
```

- [ ] **Step 3: Commit**

```bash
git add src/fleet_rlm/features/scaffold/skills/rlm-debug/scripts/diagnose.py
git commit -m "fix(scaffold): update diagnose.py import to use cli.runners"
```

---

### Task 5: Apply Ruff Formatting Fixes to Backend & Tests

**Files:**
- Modify: `src/fleet_rlm/api/config.py` (remove `# type: ignore` comments on computed_field/model_validator)
- Modify: `src/fleet_rlm/api/routers/ws/commands.py` (multi-line import formatting)
- Modify: `src/fleet_rlm/api/routers/ws/lifecycle.py` (multi-line import formatting)
- Modify: `tests/unit/fixtures_daytona.py` (multi-line import formatting)
- Modify: `tests/unit/test_daytona_rlm_runner_recursion.py` (multi-line import formatting)
- Modify: `tests/unit/test_daytona_rlm_sandbox.py` (multi-line import formatting)
- Modify: `tests/unit/test_daytona_rlm_smoke.py` (multi-line import formatting)
- Modify: `tests/unit/test_daytona_workbench_chat_agent.py` (multi-line import formatting)

These are all ruff auto-format changes: removing stale `# type: ignore` directives and reformatting long import lines into multi-line style.

- [ ] **Step 1: Stage all formatting changes**

```bash
git add src/fleet_rlm/api/config.py \
        src/fleet_rlm/api/routers/ws/commands.py \
        src/fleet_rlm/api/routers/ws/lifecycle.py \
        tests/unit/fixtures_daytona.py \
        tests/unit/test_daytona_rlm_runner_recursion.py \
        tests/unit/test_daytona_rlm_sandbox.py \
        tests/unit/test_daytona_rlm_smoke.py \
        tests/unit/test_daytona_workbench_chat_agent.py
```

- [ ] **Step 2: Run ruff format check to confirm no remaining issues**

```bash
uv run ruff format --check src/fleet_rlm/api/config.py src/fleet_rlm/api/routers/ws/commands.py src/fleet_rlm/api/routers/ws/lifecycle.py
```
Expected: All files unchanged (already formatted).

- [ ] **Step 3: Commit**

```bash
git commit -m "style: apply ruff formatting to api and test imports"
```

---

### Task 6: Commit New Backend Modules

**Files:**
- Create (already exists, untracked): `src/fleet_rlm/core/execution/profiles.py` — `ExecutionProfile` enum extracted to its own module.
- Create (already exists, untracked): `src/fleet_rlm/core/models/streaming.py` — Pydantic models for CLI runtime session config.

- [ ] **Step 1: Review the new files**

```bash
cat src/fleet_rlm/core/execution/profiles.py
cat src/fleet_rlm/core/models/streaming.py
```
Verify they contain the `ExecutionProfile` enum and `ProfileConfig`/`SessionConfig` models respectively.

- [ ] **Step 2: Commit**

```bash
git add src/fleet_rlm/core/execution/profiles.py \
        src/fleet_rlm/core/models/streaming.py
git commit -m "feat(core): add ExecutionProfile enum and CLI streaming models"
```

---

### Task 7: Commit New Backend Test Files

**Files:**
- Create (already exists, untracked): `tests/unit/test_core_models_runtime_modules.py`
- Create (already exists, untracked): `tests/unit/test_core_tools_document.py`
- Create (already exists, untracked): `tests/unit/test_document_sources_new.py`
- Create (already exists, untracked): `tests/unit/test_stream_event_model.py`
- Create (already exists, untracked): `tests/unit/test_streaming_hitl.py`

- [ ] **Step 1: Run the new tests to verify they pass**

```bash
uv run pytest -q tests/unit/test_core_models_runtime_modules.py \
                   tests/unit/test_core_tools_document.py \
                   tests/unit/test_document_sources_new.py \
                   tests/unit/test_stream_event_model.py \
                   tests/unit/test_streaming_hitl.py \
               -m "not live_llm and not benchmark" -v
```
Expected: All tests PASS.

- [ ] **Step 2: Commit**

```bash
git add tests/unit/test_core_models_runtime_modules.py \
        tests/unit/test_core_tools_document.py \
        tests/unit/test_document_sources_new.py \
        tests/unit/test_stream_event_model.py \
        tests/unit/test_streaming_hitl.py
git commit -m "test: add unit tests for core models, tools, document sources, and streaming"
```

---

### Task 8: Commit Docs Updates

**Files:**
- Modify: `AGENTS.md` (added test marker examples and cache cleanup command)
- Create (already exists, untracked): `docs/wiring-analysis.md` (comprehensive backend↔frontend wiring analysis)

- [ ] **Step 1: Review the AGENTS.md diff**

```bash
git diff HEAD -- AGENTS.md
```
Expected: Adds `pytest -q -m "live_llm"` and `pytest -q -m "live_daytona"` examples, and a cache cleanup command.

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md docs/wiring-analysis.md
git commit -m "docs: add wiring analysis and expand AGENTS.md test commands"
```

---

### Task 9: Run Backend Validation Gate

- [ ] **Step 1: Run ruff lint**

```bash
uv run ruff check src tests
```
Expected: 0 errors.

- [ ] **Step 2: Run ruff format check**

```bash
uv run ruff format --check src tests
```
Expected: All files unchanged.

- [ ] **Step 3: Run type check**

```bash
uv run ty check src --exclude "src/fleet_rlm/_scaffold/**"
```
Expected: 0 errors.

- [ ] **Step 4: Run fast test suite**

```bash
make test-fast
```
Expected: All tests pass.

- [ ] **Step 5: If any failures, fix and amend the relevant commit before proceeding.**

---

## Chunk 2: Frontend Stabilization & Commit

The frontend has ~114 changed files from three concurrent migrations:
1. React Router → TanStack Router (file-based routing in `src/routes/`)
2. Custom `ai-elements` components → `prompt-kit` library
3. Radix UI primitives → `@base-ui/react`

These need to be committed in logical order.

### Task 10: Commit TanStack Router Migration

**Files:**
- Modify: `src/frontend/src/router.tsx` (new TanStack router instance)
- Modify: `src/frontend/src/routeTree.gen.ts` (auto-generated route tree)
- Modify: `src/frontend/src/main.tsx` (updated to use TanStack RouterProvider)
- Modify: `src/frontend/src/routes/__root.tsx` (root route with Outlet)
- Modify: `src/frontend/src/routes/app.tsx` (app layout route)
- Modify: `src/frontend/src/routes/app/index.tsx`
- Modify: `src/frontend/src/routes/app/workspace.tsx`
- Modify: `src/frontend/src/routes/app/volumes.tsx`
- Modify: `src/frontend/src/routes/app/settings.tsx`
- Modify: `src/frontend/src/routes/app/analytics.tsx` (redirect route)
- Modify: `src/frontend/src/routes/app/memory.tsx` (redirect route)
- Modify: `src/frontend/src/routes/app/skills.tsx` (redirect route)
- Modify: `src/frontend/src/routes/app/skills.$skillId.tsx` (redirect route)
- Modify: `src/frontend/src/routes/app/taxonomy.tsx` (redirect route)
- Modify: `src/frontend/src/routes/app/taxonomy.$skillId.tsx` (redirect route)
- Modify: `src/frontend/src/routes/index.tsx`
- Modify: `src/frontend/src/routes/login.tsx`
- Modify: `src/frontend/src/routes/logout.tsx`
- Modify: `src/frontend/src/routes/settings.tsx`
- Modify: `src/frontend/src/routes/signup.tsx`
- Modify: `src/frontend/src/routes/404.tsx`
- Modify: `src/frontend/src/routes/$.tsx` (catch-all)
- Modify: `src/frontend/src/app/App.tsx`
- Modify: `src/frontend/src/app/layout/AppSidebar.tsx`
- Modify: `src/frontend/src/app/layout/DesktopShell.tsx`
- Modify: `src/frontend/src/app/layout/TopHeader.tsx`
- Modify: `src/frontend/src/app/pages/LogoutPage.tsx`
- Modify: `src/frontend/src/app/pages/NotFoundPage.tsx`
- Modify: `src/frontend/src/app/pages/RouteErrorPage.tsx`
- Modify: `src/frontend/src/stores/` (navigation store updates)
- Delete: `src/frontend/src/features/shell/IntegrationsDialog.tsx`
- Delete: `src/frontend/src/features/shell/PricingDialog.tsx`
- Delete: `src/frontend/src/features/shell/UserMenu.tsx`

- [ ] **Step 1: Stage router migration files**

```bash
cd src/frontend
git add src/router.tsx src/routeTree.gen.ts src/main.tsx \
        src/routes/ \
        src/app/App.tsx src/app/layout/ src/app/pages/ \
        src/stores/ \
        src/features/shell/
```

- [ ] **Step 2: Commit**

```bash
git commit -m "refactor(frontend): migrate to TanStack Router with file-based routing"
```

---

### Task 11: Commit Base UI Migration (Radix → @base-ui/react)

**Files:**
- Modify: `src/frontend/src/components/ui/dialog.tsx`
- Modify: `src/frontend/src/components/ui/dropdown-menu.tsx`
- Modify: `src/frontend/src/components/ui/popover.tsx`
- Modify: `src/frontend/src/components/ui/scroll-area.tsx`
- Modify: `src/frontend/src/components/ui/select.tsx`
- Modify: `src/frontend/src/components/ui/sheet.tsx`
- Modify: `src/frontend/src/components/ui/tabs.tsx`
- Modify: `src/frontend/src/components/ui/tooltip.tsx`
- Modify: `src/frontend/src/components/ui/input-group.tsx`
- Modify: `src/frontend/src/components/ui/spinner.tsx`
- Delete: `src/frontend/src/components/ui/navigation-menu.tsx`
- Delete: `src/frontend/src/components/ui/pagination.tsx`
- Delete: `src/frontend/src/components/ui/selectable-card.tsx`
- Delete: `src/frontend/src/components/ui/slider.tsx`
- Delete: `src/frontend/src/components/ui/suggestion-chip.tsx`

- [ ] **Step 1: Stage Base UI migration files**

```bash
cd src/frontend
git add src/components/ui/
```

- [ ] **Step 2: Commit**

```bash
git commit -m "refactor(frontend): migrate Radix UI primitives to @base-ui/react"
```

---

### Task 12: Commit prompt-kit Migration (ai-elements → prompt-kit)

**Files:**
- Modify/Delete: `src/frontend/src/components/prompt-kit/` (many files deleted, index.ts simplified)
- Modify: `src/frontend/src/components/prompt-kit/prompt-input.tsx`
- Modify: `src/frontend/src/components/prompt-kit/index.ts`
- Delete: ~28 prompt-kit component files (agent, artifact, audio-player, canvas, checkpoint, commit, connection, context, controls, edge, file-tree, image, jsx-preview, mic-selector, model-selector, node, open-in-chat, package-info, panel, persona, plan, queue, schema-display, snippet, speech-input, stack-trace, etc.)
- Delete: `src/frontend/src/components/chat/input/AgentDropdown.tsx`
- Delete: `src/frontend/src/components/chat/input/ThinkButton.tsx`
- Delete: `src/frontend/src/components/chat/input/__tests__/ThinkButton.test.tsx`
- Delete: `src/frontend/src/components/prompt-kit/__tests__/prompt-input.textarea.test.tsx`

- [ ] **Step 1: Stage prompt-kit migration files**

```bash
cd src/frontend
git add src/components/prompt-kit/ src/components/chat/
```

- [ ] **Step 2: Commit**

```bash
git commit -m "refactor(frontend): migrate ai-elements to prompt-kit and remove unused components"
```

---

### Task 13: Commit Workspace & Feature File Updates

**Files:**
- Modify: `src/frontend/src/features/rlm-workspace/assistant-content/AssistantTurnContent.tsx`
- Modify: `src/frontend/src/features/rlm-workspace/assistant-content/ExecutionDetailsGroup.tsx`
- Modify: `src/frontend/src/features/rlm-workspace/chat-shell/WorkspaceChatMessageItem.tsx`
- Modify: `src/frontend/src/features/rlm-workspace/chat-shell/tracePartRenderers.tsx`
- Modify: `src/frontend/src/features/artifacts/ArtifactTimeline.tsx`
- Delete: `src/frontend/src/features/artifacts/CodeArtifact.tsx`

- [ ] **Step 1: Stage workspace/feature updates**

```bash
cd src/frontend
git add src/features/
```

- [ ] **Step 2: Commit**

```bash
git commit -m "refactor(frontend): update workspace and artifact features for new component imports"
```

---

### Task 14: Commit Remaining Frontend Changes

**Files:**
- Modify: `src/frontend/src/styles/tailwind.css` (new utility additions)
- Modify: `src/frontend/src/styles/theme.css` (new design tokens)
- Modify: `src/frontend/src/hooks/` (hook updates for new router/state)
- Modify: `src/frontend/src/lib/rlm-api/generated/openapi.ts` (regenerated types)
- Modify: `src/frontend/openapi/fleet-rlm.openapi.yaml` (synced spec)
- Modify: `src/frontend/package.json` (dependency updates)
- Modify: `src/frontend/AGENTS.md` (updated frontend guidelines)
- Modify: `src/frontend/.agent/skills/ai-elements/` (reference updates)
- Modify: `src/frontend/src/vite-env.d.ts`

- [ ] **Step 1: Stage all remaining frontend changes**

```bash
cd src/frontend
git add .
```

- [ ] **Step 2: Commit**

```bash
git commit -m "chore(frontend): update styles, API types, dependencies, and agent docs"
```

---

### Task 15: Run Frontend Validation Gate

- [ ] **Step 1: Run TypeScript check**

```bash
cd src/frontend
pnpm run type-check
```
Expected: 0 errors. If relaxed mode needed: `pnpm run type-check:relaxed`

- [ ] **Step 2: Run lint**

```bash
pnpm run lint
```
Expected: 0 errors.

- [ ] **Step 3: Run unit tests**

```bash
pnpm run test:unit
```
Expected: All tests pass.

- [ ] **Step 4: Run build**

```bash
pnpm run build
```
Expected: Build succeeds.

- [ ] **Step 5: If any failures, fix and amend the relevant commit before proceeding.**

If TypeScript errors reference deleted components, check that all import consumers have been updated. Common pattern: a file still importing from `@/components/prompt-kit/agent` when that file was deleted — update to use the new prompt-kit package export.

---

## Chunk 3: Full Integration Validation & Final Cleanup

### Task 16: Run Full Quality Gate

- [ ] **Step 1: Run the full backend quality gate**

```bash
make quality-gate
```
Expected: All checks pass (lint, format, typecheck, tests, metadata, frontend).

- [ ] **Step 2: Run focused backend/runtime test coverage**

```bash
uv run pytest -q tests/ui/server/test_api_contract_routes.py \
                  tests/ui/server/test_router_runtime.py \
                  tests/ui/ws/test_chat_stream.py \
                  tests/unit/test_ws_chat_helpers.py \
              -m "not live_llm and not benchmark"
```
Expected: All pass.

- [ ] **Step 3: Run Daytona-focused test coverage**

```bash
uv run pytest -q tests/unit/test_daytona_rlm_config.py \
                  tests/unit/test_daytona_rlm_smoke.py \
                  tests/unit/test_daytona_rlm_sandbox.py \
                  tests/unit/test_daytona_rlm_runner.py \
                  tests/unit/test_daytona_rlm_cli.py \
              -m "not live_llm and not benchmark"
```
Expected: All pass.

- [ ] **Step 4: Verify API server starts**

```bash
timeout 10 uv run fleet-rlm serve-api --port 8099 2>&1 | head -20
```
Expected: FastAPI startup log showing routers mounted successfully, no import errors.

---

### Task 17: Verify OpenAPI Sync Integrity

- [ ] **Step 1: Regenerate OpenAPI types and check for drift**

```bash
cd src/frontend
pnpm run api:sync
git diff --exit-code -- openapi/fleet-rlm.openapi.yaml src/lib/rlm-api/generated/openapi.ts
```
Expected: No diff (types already in sync). If there IS a diff, the openapi.yaml has changed since last sync — commit the updated types.

- [ ] **Step 2: If drift detected, commit the sync**

```bash
git add openapi/fleet-rlm.openapi.yaml src/lib/rlm-api/generated/openapi.ts
git commit -m "chore(frontend): sync OpenAPI spec and regenerate types"
```

---

### Task 18: Verify Clean Working Tree

- [ ] **Step 1: Check git status**

```bash
git status --short
```
Expected: Clean working tree (0 modified, 0 untracked files relevant to the project).

- [ ] **Step 2: Run final sanity check**

```bash
make test-fast
```
Expected: All tests pass.

- [ ] **Step 3: Log the commit summary**

```bash
git log --oneline -15
```

Verify the commit history reads as a clean, logical sequence:
1. `fix(core): update TYPE_CHECKING imports in core/tools to point to core/agent`
2. `fix(core): fix runtime_factory TYPE_CHECKING import path after restructure`
3. `fix(features): update terminal commands import to use core.agent.commands`
4. `fix(scaffold): update diagnose.py import to use cli.runners`
5. `style: apply ruff formatting to api and test imports`
6. `feat(core): add ExecutionProfile enum and CLI streaming models`
7. `test: add unit tests for core models, tools, document sources, and streaming`
8. `docs: add wiring analysis and expand AGENTS.md test commands`
9. `refactor(frontend): migrate to TanStack Router with file-based routing`
10. `refactor(frontend): migrate Radix UI primitives to @base-ui/react`
11. `refactor(frontend): migrate ai-elements to prompt-kit and remove unused components`
12. `refactor(frontend): update workspace and artifact features for new component imports`
13. `chore(frontend): update styles, API types, dependencies, and agent docs`

---

## Rollback Plan

If validation fails after commits:

1. **Single task failure:** `git revert <commit-hash>` for the specific commit, fix, re-apply.
2. **Systemic failure:** `git reset --soft HEAD~N` to unstage all N commits, investigate, re-commit with fixes.
3. **Nuclear option:** `git reset --hard a82faee` to return to the last known-good commit (the current HEAD before any of these changes are committed).

## Post-Plan Next Steps

After this stabilization is complete, the following work becomes unblocked:

1. **AGENTS.md refresh:** Update `Key Architecture Boundaries` section in root `AGENTS.md` to reference the new `core/agent/`, `core/execution/`, `infrastructure/providers/` paths instead of the old `server/`, `react/`, `daytona_rlm/` paths.
2. **Legacy removal (v0.5.0):** Execute the legacy removal checklist from `plans/PRD-fastapi-modernization.md` — remove `legacy_compat.py`, `legacy_models.py`, and deprecated CRUD routes.
3. **Frontend E2E tests:** Run `pnpm run test:e2e` once a backend server is available to validate the full TanStack Router + WebSocket integration.
4. **CI pipeline update:** Ensure GitHub Actions workflows reference the correct test paths and validation commands for the new layout.
