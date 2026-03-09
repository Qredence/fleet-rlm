# Commit and Push Strategy for Current Worktree

## Purpose
The unstaged worktree spans two initiatives already reflected in [PLANS.md](/Volumes/StorageBackup/_RLM/fleet-rlm-dspy/PLANS.md): backend DSPy-native chat/runtime simplification and frontend workspace simplification. The safest path is a short stack of reviewable commits that keeps backend/frontend protocol changes together and keeps incidental repo drift out of the feature history.

## Worktree Assessment
- Cluster A: backend/runtime behavior changes around child-RLM delegation, nested live events, execution modes, runtime context payloads, and targeted Python/WebSocket tests.
- Cluster B: frontend workspace refactor that moves chat UI/store ownership into `src/frontend/src/features/rlm-workspace/*` and `src/frontend/src/stores/*`, splits oversized rendering/adapter files, and refreshes the artifact timeline UI.
- Cluster C: documentation updates in `AGENTS.md`, `docs/SUMMARY.md`, `docs/reference/index.md`, and `docs/reference/codebase-map.md`.
- Cluster D: optional or suspicious tooling drift in `pyproject.toml`, `uv.lock`, `src/frontend/components.json`, plus untracked cache/noise in `.vite/`.

## Before the First Commit
- Keep `.vite/` out of git. It is local cache output and should not ride along with the branch.
- Decide whether `DECISION.md` and `PLANS-DECISION.md` are meant to be durable repo docs. If yes, ship them in a docs-only commit. If not, leave them local.
- Treat `pyproject.toml`, `uv.lock`, and `src/frontend/components.json` as a separate decision. They do not naturally belong in the runtime/frontend refactor history.
- Expect to use `git add -p` in mixed files:
  - `src/frontend/src/features/rlm-workspace/RlmWorkspace.tsx`
  - `src/frontend/src/features/rlm-workspace/useBackendChatRuntime.ts`
  - `src/frontend/src/features/rlm-workspace/ChatMessageList.tsx`
  - `src/frontend/src/features/rlm-workspace/backendChatEventAdapter.ts`
  - `src/frontend/src/lib/data/types.ts`

## Recommended Commit Stack

### 1. `refactor(frontend): simplify workspace ownership and trace rendering`
Scope:
- Move `ClarificationCard` and `ConversationHistory` into `src/frontend/src/features/rlm-workspace/*`.
- Move the chat Zustand store into `src/frontend/src/stores/chatStore.ts` and move its tests into `src/frontend/src/stores/__tests__/chatStore.test.ts`.
- Remove old `src/frontend/src/screens/chat/*` ownership seams and `src/frontend/src/stores/mockStateStore.ts` if it is truly dead.
- Extract helper modules from oversized UI files:
  - `src/frontend/src/features/rlm-workspace/chatDisplayItems.ts`
  - `src/frontend/src/features/rlm-workspace/backendChatEventReferences.ts`
  - `src/frontend/src/features/rlm-workspace/backendChatEventToolParts.ts`
- Keep `src/frontend/src/features/rlm-workspace/ChatMessageList.tsx`, `src/frontend/src/features/rlm-workspace/backendChatEventAdapter.ts`, and `src/frontend/src/components/domain/artifacts/ArtifactTimeline.tsx` focused on the refactor/presentation changes only.

Why this should be first:
- It reduces file concentration and path churn before the cross-stack execution-mode feature lands.
- It is the cleanest review boundary for the frontend simplification work already described in [PLANS.md](/Volumes/StorageBackup/_RLM/fleet-rlm-dspy/PLANS.md).

Validation:
- `cd src/frontend && bun run type-check`
- `cd src/frontend && bun run test:unit src/stores/__tests__/chatStore.test.ts src/features/rlm-workspace/__tests__/backendChatEventAdapter.test.ts src/features/rlm-workspace/__tests__/ChatMessageList.ai-elements.test.tsx src/features/rlm-workspace/__tests__/RlmWorkspace.runtime-warning.test.tsx`

### 2. `feat(chat): add execution modes and child-RLM streaming delegation`
Scope:
- Backend/runtime files:
  - `src/fleet_rlm/core/driver.py`
  - `src/fleet_rlm/core/interpreter.py`
  - `src/fleet_rlm/react/agent.py`
  - `src/fleet_rlm/react/commands.py`
  - `src/fleet_rlm/react/delegate_sub_agent.py`
  - `src/fleet_rlm/react/rlm_runtime_modules.py`
  - `src/fleet_rlm/react/signatures.py`
  - `src/fleet_rlm/react/streaming.py`
  - `src/fleet_rlm/react/streaming_context.py`
  - `src/fleet_rlm/react/tool_delegation.py`
  - `src/fleet_rlm/react/tools/__init__.py`
  - `src/fleet_rlm/react/tools/delegate.py`
  - `src/fleet_rlm/react/trajectory_errors.py`
  - `src/fleet_rlm/react/validation.py`
  - `src/fleet_rlm/server/routers/ws/chat_connection.py`
  - `src/fleet_rlm/server/routers/ws/streaming.py`
  - `src/fleet_rlm/server/schemas/core.py`
- Backend tests:
  - `tests/ui/ws/_fakes.py`
  - `tests/ui/ws/test_chat_stream.py`
  - `tests/unit/test_rlm_state.py`
  - `tests/unit/test_tools_sandbox.py`
  - `tests/unit/test_ws_chat_helpers.py`
- Frontend pieces that belong to the same contract:
  - `src/frontend/src/components/chat/input/ExecutionModeDropdown.tsx`
  - `src/frontend/src/components/chat/input/__tests__/ExecutionModeDropdown.test.tsx`
  - `src/frontend/src/components/chat/ChatInput.tsx`
  - `src/frontend/src/features/rlm-workspace/RlmWorkspace.tsx`
  - `src/frontend/src/features/rlm-workspace/runtime-types.ts`
  - `src/frontend/src/features/rlm-workspace/useBackendChatRuntime.ts`
  - `src/frontend/src/lib/data/types.ts`
  - `src/frontend/src/lib/rlm-api/wsTypes.ts`
- If runtime-context display changes are mixed into `ChatMessageList.tsx` or `backendChatEventAdapter.ts`, stage those hunks with this commit rather than the pure refactor commit.

Why this should stay together:
- `execution_mode`, `RLM_ROOT`, `sandbox_id`, and child-RLM stream forwarding are one backend/frontend contract.
- Splitting the UI selector from the server/schema changes would create an intermediate branch state that is harder to reason about and harder to test end-to-end.

Validation:
- `uv run pytest -q tests/ui/server/test_server_config.py tests/ui/ws/test_chat_stream.py tests/unit/test_rlm_state.py tests/unit/test_tools_sandbox.py tests/unit/test_ws_chat_helpers.py`
- `cd src/frontend && bun run type-check`
- `cd src/frontend && bun run test:unit src/features/rlm-workspace/__tests__/backendChatEventAdapter.test.ts src/features/rlm-workspace/__tests__/ChatMessageList.ai-elements.test.tsx src/features/rlm-workspace/__tests__/RlmWorkspace.runtime-warning.test.tsx src/components/chat/input/__tests__/ExecutionModeDropdown.test.tsx`

### 3. `docs: sync chat/runtime guidance and codebase map`
Scope:
- `AGENTS.md`
- `docs/SUMMARY.md`
- `docs/reference/index.md`
- `docs/reference/codebase-map.md`
- Optionally `DECISION.md` and `PLANS-DECISION.md` if the branch should keep the architecture decision record in-repo.

Why this should be separate:
- The docs clearly describe the new architecture, but they are secondary evidence, not the behavioral change itself.
- A docs-only commit gives reviewers a clean way to check for drift and keeps generated/map-heavy diffs out of the feature commit.

Validation:
- `rg -n "chat_execution_mode|ChatOrchestrator|screens/chat/stores" AGENTS.md docs src || true`

### 4. `chore(tooling): isolate dependency and registry drift`
Scope:
- `pyproject.toml`
- `uv.lock`
- `src/frontend/components.json`

Ship this only if intentional:
- The Python dependency change adds `ripgrep`, but the existing code imports `ripgrepy`. Confirm the module/package mapping before committing.
- The frontend `components.json` registry update looks like tooling setup, not product behavior. It should not piggyback on the runtime/frontend refactor unless it was required to generate or maintain the new UI code.

Validation:
- `uv run python -c "import ripgrepy; print(ripgrepy.__name__)"`
- `cd src/frontend && bun run check`

## Push Strategy
- Keep commits local until Commit 2 is green. That is the first point where the backend/frontend execution-mode contract is coherent end-to-end.
- First push: after Commits 1 and 2, once the targeted backend and frontend validation commands pass.
- Second push: after Commit 3 if the docs are meant to be reviewed separately.
- Optional third push: only if Commit 4 is intentional and validated. Otherwise leave the tooling drift off the branch.
- If minimal remote churn matters more than incremental review, make all local commits first and do one push after Commit 3.

## Suggested Command Flow
```bash
# from repo root
rm -rf .vite

# commit 1
git add -p src/frontend/src/features/rlm-workspace/RlmWorkspace.tsx \
  src/frontend/src/features/rlm-workspace/useBackendChatRuntime.ts \
  src/frontend/src/features/rlm-workspace/ChatMessageList.tsx \
  src/frontend/src/features/rlm-workspace/backendChatEventAdapter.ts \
  src/frontend/src/lib/data/types.ts
git add src/frontend/src/features/rlm-workspace/ClarificationCard.tsx \
  src/frontend/src/features/rlm-workspace/ConversationHistory.tsx \
  src/frontend/src/features/rlm-workspace/chatDisplayItems.ts \
  src/frontend/src/features/rlm-workspace/backendChatEventReferences.ts \
  src/frontend/src/features/rlm-workspace/backendChatEventToolParts.ts \
  src/frontend/src/stores/chatStore.ts \
  src/frontend/src/stores/__tests__/chatStore.test.ts \
  src/frontend/src/components/domain/artifacts/ArtifactTimeline.tsx \
  src/frontend/src/features/rlm-workspace/__tests__/ChatMessageList.ai-elements.test.tsx \
  src/frontend/src/features/rlm-workspace/__tests__/backendChatEventAdapter.test.ts \
  src/frontend/src/features/rlm-workspace/__tests__/RlmWorkspace.runtime-warning.test.tsx \
  src/frontend/src/screens/chat/ClarificationCard.tsx \
  src/frontend/src/screens/chat/ConversationHistory.tsx \
  src/frontend/src/screens/chat/stores/chatStore.ts \
  src/frontend/src/screens/chat/stores/__tests__/chatStore.test.ts \
  src/frontend/src/stores/mockStateStore.ts
git commit -m "refactor(frontend): simplify workspace ownership and trace rendering"

# commit 2
git add src/fleet_rlm/core/driver.py \
  src/fleet_rlm/core/interpreter.py \
  src/fleet_rlm/react/agent.py \
  src/fleet_rlm/react/commands.py \
  src/fleet_rlm/react/delegate_sub_agent.py \
  src/fleet_rlm/react/rlm_runtime_modules.py \
  src/fleet_rlm/react/signatures.py \
  src/fleet_rlm/react/streaming.py \
  src/fleet_rlm/react/streaming_context.py \
  src/fleet_rlm/react/tool_delegation.py \
  src/fleet_rlm/react/tools/__init__.py \
  src/fleet_rlm/react/tools/delegate.py \
  src/fleet_rlm/react/trajectory_errors.py \
  src/fleet_rlm/react/validation.py \
  src/fleet_rlm/server/routers/ws/chat_connection.py \
  src/fleet_rlm/server/routers/ws/streaming.py \
  src/fleet_rlm/server/schemas/core.py \
  tests/ui/ws/_fakes.py \
  tests/ui/ws/test_chat_stream.py \
  tests/unit/test_rlm_state.py \
  tests/unit/test_tools_sandbox.py \
  tests/unit/test_ws_chat_helpers.py \
  src/frontend/src/components/chat/input/ExecutionModeDropdown.tsx \
  src/frontend/src/components/chat/input/__tests__/ExecutionModeDropdown.test.tsx \
  src/frontend/src/components/chat/ChatInput.tsx \
  src/frontend/src/features/rlm-workspace/RlmWorkspace.tsx \
  src/frontend/src/features/rlm-workspace/runtime-types.ts \
  src/frontend/src/features/rlm-workspace/useBackendChatRuntime.ts \
  src/frontend/src/lib/data/types.ts \
  src/frontend/src/lib/rlm-api/wsTypes.ts
git add -p src/frontend/src/features/rlm-workspace/ChatMessageList.tsx \
  src/frontend/src/features/rlm-workspace/backendChatEventAdapter.ts \
  src/frontend/src/features/rlm-workspace/__tests__/ChatMessageList.ai-elements.test.tsx \
  src/frontend/src/features/rlm-workspace/__tests__/backendChatEventAdapter.test.ts
git commit -m "feat(chat): add execution modes and child-RLM streaming delegation"

# first push after commit 2 validation
git push -u origin <branch>

# commit 3
git add AGENTS.md docs/SUMMARY.md docs/reference/index.md docs/reference/codebase-map.md
git add DECISION.md PLANS-DECISION.md
git commit -m "docs: sync chat runtime guidance and codebase map"
git push

# optional commit 4
git add pyproject.toml uv.lock src/frontend/components.json
git commit -m "chore(tooling): isolate dependency and registry drift"
git push
```

## Bottom Line
The most important strategic choice is to keep the backend/frontend execution-mode contract together, keep docs/tooling out of the feature history until the product behavior is green, and avoid letting `.vite/` or other incidental drift muddy the stack.
