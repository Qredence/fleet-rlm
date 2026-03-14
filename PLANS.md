# Execution Plans

## Cleanup Modernization Sweep

### Purpose

- Reduce low-risk legacy and readability debt in the Daytona and MLflow
  runtime surface without changing supported behavior.
- Align a first cleanup slice with modern Python 3.10+ best practices:
  postponed annotations, `collections.abc` callable imports, and
  `contextlib.suppress(...)` for intentionally ignored cleanup failures.
- Keep the change set reviewable by focusing on safe modernization and a few
  concrete implementation improvements instead of a repo-wide churn pass.

### Progress

- [x] Scan the repo and existing cleanup notes to find high-confidence targets.
- [x] Pull best-practice guidance via Context7 for Ruff/Python modernization.
- [ ] Land the first cleanup slice across Daytona + MLflow runtime modules.
- [ ] Re-run formatting, lint, type checks, and targeted tests.
- [ ] Commit, push, and confirm CI returns green.

### Validation

- Planned local checks for this sweep:
  - `uv run ruff format src tests`
  - `uv run ruff check src tests`
  - `uv run ty check src --exclude "src/fleet_rlm/_scaffold/**" --exclude "src/fleet_rlm/analytics/**" --exclude "src/fleet_rlm/daytona_rlm/**"`
  - `uv run pytest -q tests/unit/test_daytona_rlm_runner.py tests/unit/test_daytona_rlm_sandbox.py tests/unit/test_mlflow_integration.py tests/ui/server/test_router_runtime.py`

## Daytona Analyst Workspace For Large-Corpus Q&A

### Purpose

- Rebuild the public Daytona web/runtime contract around the official Daytona +
  DSPy host-loop RLM model instead of the older recursive run-tree framing.
- Make large-corpus analyst workflows a first-class use case: staged document
  corpora, repo context, evidence-backed Q&A, and follow-up diligence chat in
  the same workspace session.
- Recompose the Daytona frontend so chat remains conversational while the
  builder panel becomes an analyst workbench using shadcn/ui composition and
  the existing ai-elements chat surface.

### Progress

- [x] Shift Daytona terminal payloads to a trajectory-first public `run_result`
      contract with `iterations`, `callbacks`, `prompts`, `sources`,
      `attachments`, and final typed output.
- [x] Rebuild the Daytona workbench state/UI around analyst tabs instead of the
      previous node tree and selected-node timeline model.
- [x] Preserve task-first Daytona chat with optional repo/context inputs and
      workspace-session reuse across turns.
- [x] Tighten Daytona document staging so image-only/scanned PDFs fail with an
      explicit OCR-required context-stage diagnostic.
- [x] Refresh repository docs to describe the analyst workspace and host-loop
      contract rather than the deprecated recursive tree framing.
- [ ] Re-run frontend type-check and focused Vitest coverage once `bun`/`node`
      are available in the local environment.

### Delivered Behavior

- `DaytonaWorkbenchChatAgent` now emits final payloads that hydrate the
  frontend directly as analyst-oriented run state instead of requiring the UI
  to reconstruct a recursive tree from internal node structures.
- `DaytonaRunResult.to_public_dict()` publishes ordered iterations, semantic
  callback metadata, prompt handles, staged corpus sources, evidence excerpts,
  attachments, and the final typed DSPy output while preserving the richer
  internal node graph for persisted artifacts.
- The right-hand Daytona workbench now centers on `Iterations`, `Evidence`,
  `Callbacks`, `Prompts`, and `Final` tabs with shadcn `Card`, `Tabs`, `Alert`,
  `Badge`, `ScrollArea`, `Separator`, `Skeleton`, and `Empty` composition.
- Local corpus staging now fails clearly for scanned/image-only PDFs by
  surfacing an OCR-required Daytona context-stage diagnostic instead of
  pretending the files were analyzable.

### Validation

- Backend checks executed in the current environment:
  - `.venv/bin/python -m pytest -q tests/unit/test_daytona_rlm_runner.py tests/unit/test_daytona_rlm_chat_agent.py tests/unit/test_daytona_workbench_chat_agent.py tests/ui/ws/test_chat_stream.py tests/ui/server/test_router_runtime.py tests/unit/test_ws_chat_helpers.py`
- Additional backend coverage added:
  - analyst-public `run_result` serialization coverage in
    `tests/unit/test_daytona_rlm_runner.py`
  - OCR-required context-stage diagnostics in
    `tests/unit/test_daytona_rlm_sandbox.py`
- Pending when the JS toolchain is available:
  - `cd src/frontend && bun run test:unit src/features/rlm-workspace/run-workbench/__tests__/runWorkbenchAdapter.test.ts src/features/rlm-workspace/run-workbench/__tests__/RunWorkbench.test.tsx src/features/rlm-workspace/__tests__/RlmWorkspace.daytona-workbench.test.tsx src/features/rlm-workspace/__tests__/backendChatEventAdapter.test.ts`
  - `cd src/frontend && bun run type-check`

### Notes

- This plan supersedes the older Daytona run-tree expectations for the public
  web workbench. Internal node graphs still exist in persisted artifacts, but
  the supported UI contract is analyst-oriented and trajectory-first.
- Daytona websocket callers no longer send request-side `max_depth`; the UI
  still renders `runtime.max_depth` from streamed execution metadata.

## Daytona DSPy Workbench Session Runtime

### Purpose

- Move the Daytona web path off the one-shot websocket adapter and onto a
  persistent DSPy-native chat agent/session flow.
- Keep Daytona as a dedicated workbench runtime with chat output plus
  structured recursive tree events.
- Generalize the Daytona workbench from repo-analysis framing to
  general-purpose recursive RLM reasoning with optional repo/context inputs.

### Progress

- [x] Add a `DaytonaWorkbenchChatAgent` that keeps Daytona history/session
      state, optional loaded docs, and recursive run metadata in one agent.
- [x] Route `runtime_mode="daytona_pilot"` through the shared websocket
      session/streaming lifecycle instead of a Daytona-only streaming branch.
- [x] Build the Daytona runtime from the first websocket message so Daytona
      runs do not require Modal startup/config just to begin.
- [x] Remove Daytona source-setup requirements from the default workspace chat
      path while keeping optional repo/context controls compatible.
- [x] Surface Daytona readiness through `/api/v1/runtime/status` and the
      workspace warning banner.
- [x] Refresh repo docs/tests for the new Daytona contract.

### Delivered Behavior

- Daytona websocket chat now uses a dedicated DSPy module/signature-backed
  agent in `src/fleet_rlm/daytona_rlm/chat_agent.py`.
- The shared websocket loop now forwards Daytona repo/context/depth inputs into
  `agent.aiter_chat_turn_stream(...)` instead of bypassing the agent/session
  stack.
- Daytona session state now persists through the existing export/import path
  used by websocket session switching, with `history_turns` reflecting real
  multi-turn state rather than a hard-coded `1`.
- The frontend Daytona path now stays task-first: chat remains visible, the
  workbench still receives structured run events, and repo/context fields are
  only sent when explicitly provided.
- Runtime status now exposes Daytona env preflight (`DAYTONA_API_KEY`,
  `DAYTONA_API_URL`, optional `DAYTONA_TARGET`) so Daytona mode can show a
  setup-specific warning banner.

### Validation

- `uv run pytest -q tests/ui/ws/test_chat_stream.py tests/unit/test_ws_chat_helpers.py tests/ui/server/test_router_runtime.py tests/ui/server/test_server_config.py tests/ui/server/test_api_contract_routes.py`
- `uv run ruff check src/fleet_rlm/daytona_rlm/chat_agent.py src/fleet_rlm/runners.py src/fleet_rlm/server/routers/ws/api.py src/fleet_rlm/server/routers/ws/chat_connection.py src/fleet_rlm/server/routers/ws/chat_runtime.py src/fleet_rlm/server/routers/ws/streaming.py src/fleet_rlm/server/routers/ws/turn.py src/fleet_rlm/server/routers/runtime.py src/fleet_rlm/server/runtime_settings.py tests/ui/ws/_fakes.py tests/ui/ws/conftest.py tests/ui/ws/test_chat_stream.py`
- `cd src/frontend && bun run test:unit src/stores/__tests__/chatStore.test.ts src/features/rlm-workspace/__tests__/RlmWorkspace.daytona-workbench.test.tsx src/features/rlm-workspace/__tests__/useBackendChatRuntime.daytona-error.test.tsx src/features/rlm-workspace/__tests__/RlmWorkspace.runtime-warning.test.tsx src/components/chat/__tests__/ChatInput.test.tsx`
- `cd src/frontend && bun run type-check`

## Daytona Workspace Expansion

### Purpose

- Expand the experimental Daytona pilot from repo-only analysis into a
  workspace-oriented recursive RLM runtime.
- Keep Daytona self-orchestrated end to end: optional repo clone, optional
  staged local document/directory context, optional mixed-source runs, and
  pure reasoning-only runs all stay inside the Daytona path rather than
  falling back to Modal document-loading flows.
- Preserve repo-only behavior as the compatibility baseline while exposing the
  richer source model consistently across CLI, websocket, runtime metadata,
  and the dedicated Daytona workbench UI.

### Current State Snapshot (2026-03-11)

- `fleet-rlm daytona-rlm` now accepts an optional `--repo`, repeatable
  `--context-path`, or neither for reasoning-only runs. `--ref` remains valid
  only when `--repo` is present. `daytona-smoke` stays repo-only.
- Daytona workspace bootstrap is generalized:
  - clone a repo when `repo_url` is present,
  - create an empty workspace when it is not,
  - stage resolved host files/directories into `.fleet-rlm/context/`,
  - persist a manifest describing staged sources,
  - mirror the same staged context into child sandboxes for recursive calls.
- Shared document extraction has been centralized in
  `src/fleet_rlm/document_ingestion.py` so Modal document tools and Daytona
  context staging use the same PDF / DOCX / text ingestion behavior.
- The websocket contract now supports optional Daytona `repo_url`,
  optional `context_paths`, typed validation for `repo_ref` without `repo_url`,
  and Daytona runtime metadata labeled with `daytona_mode="recursive_rlm"`.
- The `RLM Workspace` Daytona UI is now source-oriented:
  - repository URL stays optional,
  - local context paths are entered manually one per line,
  - source state is rendered as `Repo`, `Repo + local context`,
    `Local context only`, or `Reasoning only`,
  - the composer shows a non-interactive Daytona RLM indicator while Daytona
    is selected,
  - the workbench header summarizes repo plus local staged sources and falls
    back to `No external sources` for reasoning-only runs.

### Delivery Decisions

- Keep Daytona-specific local context staging Daytona-native. Do not route
  Daytona document/directory inputs through `docs_path`, attachment upload, or
  other Modal-only top-level execution paths.
- Keep helper names such as `grep_repo` and `chunk_file` for compatibility even
  though the runtime now reasons over a broader workspace.
- Preserve `repo` in persisted Daytona results and streamed payloads for
  backward compatibility, but allow it to be empty when no repo was provided.
- Use `context_sources` as the canonical structured metadata for staged local
  sources in run results, node payloads, and websocket final payloads.
- Keep `execution_mode` Modal-only in the UI/backend contract. Daytona runtime
  labeling belongs in `runtime_mode` plus `daytona_mode`.

### Validation Targets

- `uv run pytest -q tests/unit/test_daytona_rlm_sandbox.py tests/unit/test_daytona_rlm_runner.py tests/unit/test_daytona_rlm_cli.py tests/unit/test_ws_chat_helpers.py tests/ui/ws/test_chat_stream.py`
- `uv run ruff check src/fleet_rlm/daytona_rlm src/fleet_rlm/server/routers/ws src/fleet_rlm/server/schemas tests/unit/test_daytona_rlm_sandbox.py tests/unit/test_daytona_rlm_runner.py tests/unit/test_daytona_rlm_cli.py tests/unit/test_ws_chat_helpers.py tests/ui/ws/test_chat_stream.py`
- `cd src/frontend && bun run test:unit src/features/rlm-workspace/daytona-workbench/__tests__/daytonaWorkbenchAdapter.test.ts src/features/rlm-workspace/daytona-workbench/__tests__/DaytonaWorkbench.test.tsx src/features/rlm-workspace/__tests__/DaytonaSetupCard.test.tsx src/features/rlm-workspace/__tests__/RlmWorkspace.daytona-workbench.test.tsx src/stores/__tests__/chatStore.test.ts src/components/chat/__tests__/ChatInput.test.tsx src/components/chat/input/__tests__/RuntimeModeDropdown.test.tsx`
- `cd src/frontend && bun run type-check`

### Browser Testing Instructions

#### Setup

- From repo root, start the app with `uv run fleet web`.
- Wait for the local web URL, open it in a browser, and navigate to `RLM Workspace`.
- Keep browser devtools open on the Network tab so websocket errors and failed
  requests are visible during manual testing.

#### Core UI Checks

- Switch the runtime dropdown between `Modal chat` and `Daytona pilot`.
- Confirm Daytona mode shows the source-oriented setup card and the
  non-interactive `Daytona RLM` indicator instead of Modal execution-mode controls.
- Confirm the setup card allows:
  - an empty repo URL,
  - multiline local context paths,
  - advanced `repo_ref`, `max_depth`, and `batch_concurrency`.
- Confirm the workbench header summarizes external sources and can render
  `No external sources`.

#### Scenario 1: Repo-only Daytona Run

- Set runtime to `Daytona pilot`.
- Provide a valid repository URL and leave context paths empty.
- Submit a simple analysis prompt.
- Verify:
  - the source badge reads `Repo`,
  - the run begins without client-side blocking,
  - the workbench shows the repo in the header,
  - node/timeline updates stream into the Daytona workbench,
  - the final event preserves repo metadata and no local context sources.

#### Scenario 2: Local Context-only Daytona Run

- Leave repo URL empty.
- Enter one or more readable host file/directory paths, one per line.
- Submit a prompt asking Daytona to analyze those materials.
- Verify:
  - the source badge reads `Local context only`,
  - send is enabled,
  - the workbench header shows the local sources,
  - the run completes without requiring a repo URL,
  - local context sources appear in the final workbench summary.

#### Scenario 3: Repo + Local Context Daytona Run

- Provide both a valid repo URL and at least one local context path.
- Submit a task that requires both code and document context.
- Verify:
  - the source badge reads `Repo + local context`,
  - the workbench shows both the repo and the staged local sources,
  - timeline/node updates continue to stream normally,
  - the final workbench state preserves both source types.

#### Scenario 4: Reasoning-only Daytona Run

- Leave repo URL empty and clear all context paths.
- Submit a reasoning-only prompt.
- Verify:
  - the source badge reads `Reasoning only`,
  - the card explains that no external sources are configured,
  - send is still enabled,
  - the workbench header shows `No external sources`,
  - the run completes without repo-specific UI assumptions.

#### Validation and Guardrail Checks

- Enter an invalid manual repository URL and confirm the setup card blocks send
  with a clear validation message.
- Confirm `repo_ref` is only meaningful when a repo is configured.
- Confirm switching back to `Modal chat` hides the Daytona setup card and restores
  the normal execution-mode dropdown.
- During an active Daytona run, confirm the setup card can still show the active
  source mix from the current run.
- If a run fails, confirm errors surface in the Daytona workbench instead of the
  generic chat transcript.

### Deferred Follow-Ons

- Add richer UI affordances for browsing/choosing local host paths instead of
  manual newline entry if the Daytona source model proves stable.
- Revisit whether repo URL auto-detection from prompt text should remain part
  of the Daytona setup UX or move behind explicit source controls.
- Expand Daytona smoke/live coverage for staged local context once there is a
  stable opt-in live Daytona test lane for host-file staging semantics.

## Daytona Recursive Runtime & Workbench Overhaul

### Purpose

- Converge the experimental Daytona path on a true tree-based reasoning
  runtime instead of a chat-shaped wrapper around recursive work.
- Keep orchestration state, loops, aggregation, and intermediate artifacts
  inside the persistent sandbox runtime while preserving host-side control of
  costly semantic calls.
- Redesign Daytona mode in `RLM Workspace` so humans can monitor, pause, guide,
  and approve recursive branches without trying to decode a large tree through
  one linear transcript.

### Progress

- [ ] Extract the current callback bridge into an explicit Daytona broker layer.
- [ ] Replace static depth-first failure modes with budget-aware recursion and
      resumable branch state.
- [ ] Ship a dual-pane Daytona workspace with graph-first inspection.
- [ ] Add node-level pause, inject, and approval controls.
- [ ] Validate the upgraded runtime without regressing `modal_chat`.

### Current State Snapshot (2026-03-11)

- The Daytona runtime already contains part of the desired bridging pattern:
  `src/fleet_rlm/daytona_rlm/driver.py` exposes `llm_query(...)` and
  `llm_query_batched(...)` as host callbacks, and
  `src/fleet_rlm/daytona_rlm/sandbox_controller.py` executes recursive child
  sandboxes from a persistent interpreter state.
- The present limitation is structural rather than total absence:
  `sandbox_controller.py` currently owns too many responsibilities at once:
  orchestration, recursion, child lifecycle, callback transport, budgeting,
  root synthesis policy, and event shaping.
- `RolloutBudget` in `src/fleet_rlm/daytona_rlm/types.py` is still dominated by
  static ceilings (`max_depth`, `max_sandboxes`, `max_iterations`,
  `global_timeout`) rather than an adaptive compute/token budget with yield and
  resume semantics.
- The frontend Daytona experience remains chat-column-first.
  `src/frontend/src/features/rlm-workspace/RlmWorkspace.tsx` renders messages,
  composer, and setup card in one vertical flow, while
  `src/frontend/src/features/rlm-workspace/daytona-workbench/DaytonaWorkbench.tsx`
  behaves as an inspector card instead of a dedicated graph workspace.
- Existing human-in-the-loop support is message-centric.
  `backendChatEventAdapter.ts` and `ChatMessageList.tsx` already understand
  `hitl_request` / `hitl_resolved`, but the Daytona workbench store has no
  node-targeted pause, inject, budget approval, or branch resume actions.

### Desired End State

- Sandbox-generated Python stays responsible for decomposition, iteration,
  aggregation, and stateful branch execution.
- `llm_query(...)` and `llm_query_batched(...)` remain the public recursive
  helper surface, but their transport is formalized as a typed Daytona broker
  contract rather than being implicitly coupled to controller internals.
- Child branches can suspend with serialized continuation state instead of hard
  failing as soon as a static limit is crossed.
- The main Daytona UI becomes dual-pane:
  chat and approvals on the left, live execution graph/canvas on the right.
- Operators can inspect a node, pause it, inject guidance into that node’s
  local context, and approve risky actions or extra budget without leaving the
  workspace.

### File Touchpoints

- Backend runtime:
  - `src/fleet_rlm/daytona_rlm/driver.py`
  - `src/fleet_rlm/daytona_rlm/sandbox_controller.py`
  - `src/fleet_rlm/daytona_rlm/runner.py`
  - `src/fleet_rlm/daytona_rlm/protocol.py`
  - `src/fleet_rlm/daytona_rlm/types.py`
  - `src/fleet_rlm/daytona_rlm/system_prompt.py`
  - `src/fleet_rlm/daytona_rlm/results.py`
- Backend streaming / API surface:
  - `src/fleet_rlm/server/routers/ws/chat_connection.py`
  - `src/fleet_rlm/server/routers/ws/contracts.py`
  - `src/fleet_rlm/server/routers/ws/runtime_options.py`
  - `src/fleet_rlm/server/routers/ws/streaming.py`
  - `src/fleet_rlm/server/schemas/core.py`
- Frontend workspace and graph UI:
  - `src/frontend/src/features/rlm-workspace/RlmWorkspace.tsx`
  - `src/frontend/src/features/rlm-workspace/daytona-workbench/DaytonaWorkbench.tsx`
  - `src/frontend/src/features/rlm-workspace/daytona-workbench/daytonaWorkbenchAdapter.ts`
  - `src/frontend/src/features/rlm-workspace/daytona-workbench/daytonaWorkbenchStore.ts`
  - `src/frontend/src/features/rlm-workspace/backendChatEventAdapter.ts`
  - `src/frontend/src/features/rlm-workspace/ChatMessageList.tsx`
  - `src/frontend/src/features/artifacts/ArtifactGraph.tsx`
  - `src/frontend/src/features/artifacts/ArtifactCanvas.tsx`

### Workstream 1: Broker-Backed Recursion Boundary

- Preserve the current recursive helper API while tightening finalization:
  `llm_query(...)`, `llm_query_batched(...)`, `rlm_query(...)`, and
  `rlm_query_batched(...)` stay stable for sandbox-authored code, and
  finalization is `SUBMIT(...)` only.
- Extract callback transport into an explicit Daytona broker layer with a small
  typed request/response contract for:
  `llm_query`, `llm_query_batched`, cancellation, pause state, approval state,
  and branch resume.
- Treat the current stdin/stdout callback bridge as the implementation baseline.
  The refactor goal is to make the broker transport replaceable without
  rewriting sandbox-authored orchestration logic.
- Keep the broker tiny and sandbox-local. If a lightweight Flask server is used
  inside the Daytona sandbox, its surface should remain private to the runtime
  and mirror the existing callback types rather than inventing a second helper
  API.
- Move protocol ownership into `protocol.py` plus focused runtime helpers so
  `sandbox_controller.py` becomes an orchestrator host, not the place where
  every transport detail is encoded.

### Workstream 2: True Recursive Orchestration and Context Hygiene

- Keep persistent sandbox state as the primary place where loops, imports,
  aggregation maps, staged prompt objects, and partial results live.
- Shift plan execution toward “aggregate locally, submit once” behavior:
  child outputs should be parsed and merged inside the sandbox instead of being
  truncated into chat-facing text too early.
- Reserve root finalization for human-readable synthesis. Structured data is
  still valid for children, but the root path should continue to enforce a
  synthesized answer via `SUBMIT(summary=...)` or `SUBMIT(final_markdown=...)`.
- Add explicit branch snapshot/continuation objects so a node can yield for
  budget or operator input and resume without replaying the entire subtree.
- Keep Daytona-native context staging and prompt handles as the storage
  primitives for resumed work rather than leaking branch state back into the
  top-level chat transcript.

### Workstream 3: Budget-Aware Recursion

- Evolve `RolloutBudget` from static ceilings into a richer policy object with:
  remaining compute credits, remaining child budget, timeout, optional soft
  depth guidance, and thresholds that require operator approval.
- Replace “depth exceeded” as the only stop condition with cooperative yield
  states such as:
  `awaiting_budget`, `awaiting_approval`, `paused`, and `resumable_error`.
- Add a “yield for budget” path where a branch serializes its continuation
  state, surfaces the reason and estimated additional cost, and waits for a UI
  resolution instead of failing the whole run.
- Keep `max_depth` as a guardrail for pathological recursion during the
  transition, but treat it as a fallback safety belt rather than the primary
  scheduler.
- Stream budget consumption and branch state over websocket payloads so the UI
  can show the operator what is consuming budget and why.

### Workstream 4: Dual-Pane Daytona Workspace

- Rework `RlmWorkspace.tsx` so Daytona mode becomes a layout mode, not just a
  setup card plus inspector beneath the shared chat surface.
- Left pane:
  - conversation transcript,
  - branch approval prompts,
  - clarification / budget decisions,
  - composer and runtime setup.
- Right pane:
  - a graph/canvas view of the active Daytona tree,
  - node state coloring (`running`, `completed`, `error`, `paused`,
    `awaiting_budget`, `awaiting_approval`),
  - timeline / prompt / artifact drill-down for the selected node.
- Reuse `ArtifactGraph.tsx` and `ArtifactCanvas.tsx` primitives where they fit,
  but do not force Daytona nodes into artifact semantics if a dedicated
  `DaytonaGraphCanvas` becomes clearer.
- Keep a mobile fallback that collapses the dual-pane layout into tabs rather
  than trying to cram the full canvas beside chat on narrow screens.

### Workstream 5: Pause, Inject, and Approval UX

- Add node-targeted control intents:
  `pause_node`, `resume_node`, `inject_node_message`, `approve_budget`, and
  `resolve_node_gate`.
- Reuse the existing `hitl_request` / `hitl_resolved` rendering path when
  possible, but extend payloads with `run_id`, `node_id`, gate type, and
  branch-local context so approvals stay tied to a specific node.
- Define cooperative pause checkpoints in the sandbox runtime. Pausing should
  not kill the whole run unless the operator explicitly cancels the subtree.
- Injection should append an operator note or branch directive to the selected
  node’s local continuation state rather than mutating unrelated siblings.
- High-risk tools executed by child nodes should emit approval requests through
  the same node-aware gate system instead of bypassing the workbench.

### Workstream 6: Delivery Sequence

- Phase 1:
  runtime/protocol extraction, additive broker contract, and backend tests.
- Phase 2:
  resumable budget model plus node lifecycle events over websocket.
- Phase 3:
  dual-pane Daytona workspace and graph-first selection model.
- Phase 4:
  node pause/inject/approval controls wired end to end.
- Phase 5:
  cleanup, docs refresh, and broader quality gates.

### Validation Targets

- Backend unit / integration:
  - `uv run pytest -q tests/unit/test_daytona_rlm_driver.py tests/unit/test_daytona_rlm_runner.py tests/unit/test_daytona_rlm_sandbox.py tests/unit/test_daytona_rlm_cli.py tests/unit/test_ws_chat_helpers.py tests/ui/ws/test_chat_stream.py`
  - Add targeted regression coverage for the new transport and policy layers:
    `tests/unit/test_daytona_rlm_broker.py`,
    `tests/unit/test_daytona_rlm_budget.py`,
    `tests/unit/test_daytona_rlm_resume.py`
- Backend static checks:
  - `uv run ruff check src/fleet_rlm/daytona_rlm src/fleet_rlm/server/routers/ws src/fleet_rlm/server/schemas tests/unit/test_daytona_rlm_driver.py tests/unit/test_daytona_rlm_runner.py tests/unit/test_daytona_rlm_broker.py tests/unit/test_daytona_rlm_budget.py tests/unit/test_daytona_rlm_resume.py tests/ui/ws/test_chat_stream.py`
- Frontend:
  - `cd src/frontend && bun run type-check`
  - `cd src/frontend && bun run test:unit src/features/rlm-workspace/daytona-workbench/__tests__/daytonaWorkbenchAdapter.test.ts src/features/rlm-workspace/daytona-workbench/__tests__/DaytonaWorkbench.test.tsx src/features/rlm-workspace/__tests__/RlmWorkspace.daytona-workbench.test.tsx src/features/rlm-workspace/__tests__/backendChatEventAdapter.test.ts`
  - Add graph/control regressions for the new layout and node actions:
    `src/features/rlm-workspace/daytona-workbench/__tests__/DaytonaGraphCanvas.test.tsx`,
    `src/features/rlm-workspace/__tests__/RlmWorkspace.daytona-dual-pane.test.tsx`
- Manual browser checks:
  - run a Daytona task with three or more descendants and confirm the graph
    remains comprehensible without reading the raw transcript,
  - pause a node and inject a correction into that node only,
  - force a budget yield and approve extra budget from the UI,
  - verify Modal chat behavior is unchanged when `runtime_mode="modal_chat"`.

### Decision Log

- Keep `Modal chat` as the default product path; this overhaul stays Daytona-only
  until the recursive model is proven stable.
- Preserve existing helper names and websocket metadata fields where possible.
  Add capabilities and node state fields instead of renaming stable payload
  keys.
- Prefer additive protocol changes over a flag day rewrite. The current
  callback bridge is close enough to the desired architecture that migration can
  happen behind a stable helper surface.
- Reuse existing HITL rendering primitives rather than building a second,
  Daytona-only approval UI from scratch.

### Open Questions

- Should the budget unit be token-estimate based, abstract compute credits, or
  a hybrid policy that tracks both?
- Does the in-sandbox broker need to be a separate long-lived Flask process, or
  can the same contract live inside the controller/runtime process while still
  achieving the desired separation of concerns?
- Which Daytona tool classes should be treated as approval-gated by default?
- What is the right persistence format for resumable branch state:
  prompt-handle-backed JSON snapshots, workspace files, or persisted run-result
  fragments?
