-----------------------
# PLAN A

╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
## Code Simplification Plan: `src/fleet_rlm`

## Context
The recent architectural audit introduced a `ChatOrchestrator` wrapper that tried to route user queries between a standard ReAct execution and a deep recursive `dspy.RLM` execution using regex rules. This created a lot of complexity: duplicated state, manual looping over DSPy internals to extract streaming events, and brittle "stringly-typed" routing. Furthermore, the existing `rlm_query` tool spawns a full recursive sub-agent (a second ReAct loop) which is heavy and complex.

Based on discussions, we are shifting to a cleaner, DSPy-native architecture:
The standard `RLMReActChatAgent` will become the sole, primary orchestrator for the chat. When the user requests a deep analysis or code generation, the ReAct agent will autonomously choose to call the `rlm_query` tool. This tool will be refactored to bypass the heavy sub-agent and directly invoke a lightweight `dspy.RLM` module. The intermediate steps of this RLM loop (Think, Write Python, Execute, Read Output) will be streamed directly back to the UI.

## Architecture Diagram (New Logic)

```text
┌────────────────────────────────────────────────────────┐
│                   WebSocket / UI                       │
└───────────────────────▲────────────────────────────────┘
                        │ (Streams StreamEvents: tokens, tool calls,
                        │  trajectory steps like Think/Execute)
┌───────────────────────┴────────────────────────────────┐
│               RLMReActChatAgent                        │
│                                                        │
│  - Maintains conversation history                      │
│  - Predicts next thought/response                      │
│  - Selects tools                                       │
│                                                        │
│  ┌────────────────┐     ┌──────────────────────────┐   │
│  │ Standard Tools │     │ rlm_query Tool (Refactored)│ │
│  │ (read file,    │     │                          │   │
│  │  grep, etc.)   │     │  - Instantiates dspy.RLM │   │
│  └────────────────┘     │  - Passes callback for   │   │
│                         │    streaming inner events│   │
└─────────┬───────────────┴──────────────┬───────────────┘
          │                              │
          │                              ▼
          │                 ┌──────────────────────────┐
          │                 │       dspy.RLM           │
          │                 │                          │
          │                 │ 1. Think                 │
          │                 │ 2. Write Python          │
          │                 │ 3. Execute               │
          │                 │ 4. Read Output           │
          │                 │ ... repeat until         │
          │                 │ n. SUBMIT(Final Answer)  │
          │                 └────────────┬─────────────┘
          │                              │
          ▼                              ▼
┌────────────────────────────────────────────────────────┐
│                Modal Sandbox (Python REPL)             │
│                (Executes code and returns output)      │
└────────────────────────────────────────────────────────┘
```

## Key Changes

### 1. Delete `ChatOrchestrator` & Associated Logic
*   **Action:** Completely remove `src/fleet_rlm/react/chat_orchestrator.py`.
*   **Action:** Remove `RoutedRLMModule` and any other custom RLM loops built for the orchestrator.
*   **Why:** We are removing the manual regex routing and custom event interception loops in favor of letting the ReAct agent use its tools.

### 2. Make `RLMReActChatAgent` the Primary Entrypoint
*   **File:** `src/fleet_rlm/server/routers/ws/chat_runtime.py` and `src/fleet_rlm/runners.py`
*   **Action:** Update the runtime factories and WebSocket endpoints to instantiate and stream directly from `RLMReActChatAgent` instead of wrapping it in `ChatOrchestrator`.

### 3. Refactor `rlm_query` to use Direct `dspy.RLM`
*   **File:** `src/fleet_rlm/react/tools/delegate.py` and `src/fleet_rlm/react/tool_delegation.py`
*   **Action:** Rewrite the `rlm_query` tool. Instead of calling `spawn_delegate_sub_agent_async`, it will instantiate a `dspy.RLM` module (using `build_root_chat_rlm` or a similar factory).
*   **Details:**
    *   The tool will accept `prompt` and `context`.
    *   It will execute the `dspy.RLM` module using the agent's existing `ModalInterpreter`.
    *   It will return the raw text payload submitted by the RLM's `SUBMIT()` action back to the ReAct agent.
    *   **Crucially**, once the tool returns, the ReAct agent will see this final output as the `tool_result` and will then use its standard LLM pass to formulate a final conversational explanation back to the user.
    *   **Routing Logic (Addressing "How does it decide?"):** The `dspy.ReAct` agent makes autonomous decisions on which tool to call based purely on the tool's signature and docstring. Currently, the `rlm_query` tool has a vague docstring (`"""Delegate a complex sub-task to a recursive child RLM sandbox."""`). We will update this docstring to be highly explicit, e.g., `"""Use this tool to write code, solve complex problems, explore the codebase deeply, or execute deep multi-step reasoning in a Python REPL."""`. This ensures the ReAct agent's LLM knows exactly *when* to step out of normal conversation and invoke the heavy execution tool.

### 4. Mix Streams via Callback
*   **File:** `src/fleet_rlm/react/tools/delegate.py` and `src/fleet_rlm/react/streaming.py`
*   **Action:** To ensure the UI sees the "Think -> Code -> Execute" steps while the `dspy.RLM` tool runs, the outer `RLMReActChatAgent` will pass its `_live_event_callback` down to the `rlm_query` tool.
*   **Details:** The refactored `rlm_query` tool will use `dspy.streamify` on the inner `dspy.RLM` module, passing a custom listener or using the injected callback to emit `StreamEvent`s directly to the active WebSocket stream.

## Metrics & Code Tree Updates

### Estimated LOC Diff
- **Delete:** `src/fleet_rlm/react/chat_orchestrator.py` (~593 LOC)
- **Delete:** `src/fleet_rlm/react/delegate_sub_agent.py` (~198 LOC)
- **Delete:** `src/fleet_rlm/react/rlm_runtime_modules.py` (~239 LOC)
- **Modify:** `src/fleet_rlm/react/tools/delegate.py` (Add ~50 LOC for new lightweight `dspy.RLM` invocation)
- **Net Diff:** ~980 LOC removed. This is a massive simplification of the `react` package.

### Planned Code Tree (Subset for `src/fleet_rlm/react`)
```text
src/fleet_rlm/react/
├── __init__.py
├── agent.py                  # Primary entrypoint (restored)
├── commands.py
├── core_memory.py
├── document_cache.py
├── document_sources.py
├── runtime_factory.py        # Updated to serve ReAct agent directly
├── signatures.py             # Removed IntentRouterSignature
├── streaming.py              # Remains for ReAct streaming events
├── streaming_citations.py
├── streaming_context.py
├── tool_delegation.py        # Simplified to point to new rlm_query
├── tools/
│   ├── __init__.py
│   ├── chunking.py
│   ├── delegate.py           # Contains refactored `rlm_query` using direct dspy.RLM
│   ├── document.py
│   ├── filesystem.py
│   ├── memory_intelligence.py
│   ├── sandbox.py
│   └── sandbox_helpers.py
├── trajectory_errors.py
└── validation.py
```

### Impact on other `src/fleet_rlm` modules
Because the `ChatOrchestrator` was exclusively a high-level router sitting between the FastAPI websocket layer and the underlying `RLMReActChatAgent`, deleting it **has zero structural impact** on the rest of the package:
- `@src/fleet_rlm/core`: Untouched. The `ModalInterpreter` and base driver logic continue to execute code exactly as before.
- `@src/fleet_rlm/chunking`: Untouched. Document chunking heuristics remain the same.
- `@src/fleet_rlm/db`: Untouched. Chat histories and workspace state persistence are isolated from the orchestration logic.
- `@src/fleet_rlm/models`: Untouched. Pydantic models for database, stream events, and API boundaries remain unchanged.

The only external files needing updates are `src/fleet_rlm/runners.py` and `src/fleet_rlm/server/routers/ws/chat_runtime.py`, which will simply instantiate and yield from `RLMReActChatAgent` instead of `ChatOrchestrator`.

1.  **Run Tests:** Execute `pytest tests/ui/ws/` and `pytest tests/unit/` to ensure the WebSocket connection and stream parsing work directly with `RLMReActChatAgent`.
2.  **UI Verification:** In the UI, ask the agent to "write a python script to solve a maze." Verify that the outer ReAct agent calls the `rlm_query` tool, and that the UI streams the "Think, Write Python, Execute, Read Output" phases from the Modal sandbox natively.
3.  **Code Cleanup:** Run `ruff check` and ensure that all dead code related to the orchestrator and the old `delegate_sub_agent.py` has been purged.

 ------------------------



 ______________

 # PLAN B
I’m syncing the final reviewed plan into session memory first, then I’ll give you the full chat-ready version with the corrected architecture, ASCII diagrams, rationale, scope, and verification steps.

Deleted memory [](file:///Users/zocho/Library/Application%20Support/Code%20-%20Insiders/User/workspaceStorage/3d4bbb1c722d91f7648e7195628d4c67/GitHub.copilot-chat/memory-tool/memories/YzY0ZTQ2ZjQtZWJiYi00ZjgwLWEwZDgtMjFlMTNjZmI2YWJm/plan.md)

Created memory file [](file:///Users/zocho/Library/Application%20Support/Code%20-%20Insiders/User/workspaceStorage/3d4bbb1c722d91f7648e7195628d4c67/GitHub.copilot-chat/memory-tool/memories/YzY0ZTQ2ZjQtZWJiYi00ZjgwLWEwZDgtMjFlMTNjZmI2YWJm/plan.md)

## Final plan: ReAct-first chat simplification

Here’s the fully corrected final plan I’d use. It keeps the good parts of the current draft, fixes the stale assumptions, and lines up with the actual code on this branch.

At a high level, the plan is:

- make `RLMReActChatAgent` the **only** top-level chat runtime
- delete `ChatOrchestrator` and the **root-RLM routing path**
- **keep** child-RLM delegation via `rlm_query` → delegate_sub_agent.py
- add nested event forwarding so child `dspy.RLM` progress appears in the UI

The key correction is the important one:
the current `rlm_query` path is **already not a second ReAct loop**. It already launches a bounded child `dspy.RLM`. So the simplification should remove duplicated top-level orchestration, **not** rip out the child-RLM layer.

## Rationale

The current branch has two top-level chat brains:

- `RLMReActChatAgent` for ReAct chat/tool use
- `ChatOrchestrator` for top-level routing into either:
  - ReAct path
  - root chat `dspy.RLM` path

That creates unnecessary complexity:

- duplicated runtime paths
- duplicated streaming logic
- mode-routing config (`chat_execution_mode`)
- extra signatures and factories only needed for root-RLM routing
- confusing ownership over chat behavior

At the same time, the **child-RLM delegation mechanism is useful** and already reasonably aligned with DSPy:

- `rlm_query`
- `spawn_delegate_sub_agent_async(...)`
- `build_recursive_subquery_rlm(...)`

That layer is a good fit for “deep work as a tool,” especially in a chat product.

So the simplest correct architecture is:

```text
one top-level chat agent
+
child dspy.RLM as bounded escalation
```

not:

```text
one top-level ReAct mode
plus
one top-level root-RLM mode
plus
child RLM below that
```

## Architecture: current vs target

### Current architecture

```text
USER / UI
   |
   v
WebSocket /api/v1/ws/chat
   |
   v
┌──────────────────────────────┐
│ WS runtime                   │
│ chat_runtime + chat_connection
└──────────────┬───────────────┘
               |
               v
┌──────────────────────────────┐
│ ChatOrchestrator             │
│                              │
│ - routes chat mode           │
│ - owns root-RLM path         │
│ - delegates other behavior   │
└──────────────┬───────────────┘
               |
      ┌────────┴─────────┐
      |                  |
      v                  v
┌───────────────┐   ┌───────────────────┐
│ ReAct path    │   │ Root chat RLM     │
│               │   │                   │
│ RLMReAct      │   │ build_root_chat_  │
│ ChatAgent     │   │ rlm()             │
└──────┬────────┘   └─────────┬─────────┘
       |                      |
       | may call tools       | may call rlm_query
       v                      v
┌────────────────────────────────────────┐
│ rlm_query                              │
│ -> spawn_delegate_sub_agent_async()    │
│ -> child dspy.RLM                      │
└────────────────────────────────────────┘
```

### Target architecture

```text
USER / UI
   |
   v
WebSocket /api/v1/ws/chat
   |
   v
┌──────────────────────────────┐
│ WS runtime                   │
│ - preload docs if docs_path  │
│ - no top-level chat routing  │
└──────────────┬───────────────┘
               |
               v
┌──────────────────────────────┐
│ RLMReActChatAgent            │
│ sole top-level chat runtime  │
└──────────────┬───────────────┘
               |
               v
┌──────────────────────────────┐
│ ReAct chooses tools          │
│                              │
│ - normal tools               │
│ - rlm_query if deep work     │
│   is actually needed         │
└──────────────┬───────────────┘
               |
               v
┌──────────────────────────────┐
│ rlm_query                    │
│ thin wrapper                 │
└──────────────┬───────────────┘
               |
               v
┌──────────────────────────────┐
│ delegate_sub_agent.py        │
│ child-RLM executor           │
│ + nested event forwarding    │
└──────────────┬───────────────┘
               |
               v
┌──────────────────────────────┐
│ Child dspy.RLM               │
│ RecursiveSubQuerySignature   │
│ bounded REPL loop            │
└──────────────────────────────┘
```

## Why this target is better

### Simpler top-level mental model

Instead of:

- “chat may be ReAct”
- or “chat may be root RLM”
- and “either may eventually recurse”

you get:

- chat is always ReAct
- deep work is always a tool decision

That is much easier to explain, test, and debug.

### Better fit for interactive chat

This repo is a chat/runtime product with:

- session persistence
- websocket streaming
- command handling
- loaded document state
- cancellation
- turn metrics

That is a better match for:

- lightweight ReAct by default
- bounded deep escalation only when needed

than for a fully recursive top-level runtime.

### Keeps the useful part of recursion

The child-RLM layer still gives you:

- deep decomposition
- bounded recursive execution
- separate child interpreter
- delegate budget and depth control
- optional nested event visibility

So you keep the useful hammer without making every turn a nail.

## Scope: what stays, what changes, what gets deleted

## Keep

These should remain part of the architecture:

- agent.py
- delegate_sub_agent.py
- delegate.py
- streaming.py
- streaming_context.py
- child-RLM support in rlm_runtime_modules.py
- `RecursiveSubQuerySignature` in signatures.py

Behaviorally, keep:

- ReAct-first chat
- `rlm_query` as deep-work escalation
- recursion depth limits
- delegate budget per turn
- delegate LM fallback behavior
- result truncation behavior
- existing `StreamEvent` vocabulary

## Change

These should be simplified or narrowed:

- chat_runtime.py
  - instantiate direct `RLMReActChatAgent`
  - stop creating `ChatOrchestrator`
- runners.py
  - remove `build_chat_orchestrator()`
  - keep direct chat-agent builder path
- delegate.py
  - keep `rlm_query` thin
  - pass through callback into child executor
- delegate_sub_agent.py
  - add nested event forwarding
- rlm_runtime_modules.py
  - remove root-chat-specific factory only
- signatures.py
  - remove root-routing/root-chat signatures only
- config.py
  - remove `chat_execution_mode`

## Delete

These are good deletion targets:

- chat_orchestrator.py
- test_chat_orchestrator.py
- root-only routing artifacts:
  - `IntentRouterSignature`
  - `RoutedChatTurnSignature`
  - `build_root_chat_rlm()`
  - `chat_execution_mode` wiring

## Detailed implementation plan

## Phase 1 — Freeze target behavior

This phase is about making the intended runtime behavior explicit before touching structure.

1. Chat always enters via `RLMReActChatAgent`.
2. `docs_path` only means:
   - preload or reload a document into session state
   - do **not** force root-RLM execution
3. Remove the concept of top-level execution mode:
   - no `auto`
   - no `react`
   - no `rlm`
4. Child RLM remains the only recursive deep-work mechanism.

### Why this phase matters

Without freezing these decisions first, it’s too easy to “simplify” by deleting files while leaving hidden compatibility branches or reintroducing a wrapper.

## Phase 2 — Simplify the websocket/runtime entrypoint

Change the runtime bootstrap so websocket chat constructs a direct ReAct agent.

### Main files

- chat_runtime.py
- runners.py

### Work

1. Replace orchestrator construction in chat_runtime.py.
2. Remove `build_chat_orchestrator()` from runners.py.
3. Ensure the websocket code still has access to the methods it expects:
   - async lifecycle
   - `execute_command`
   - `load_document`
   - `reset`
   - `history_turns`
   - `export_session_state`
   - `import_session_state`
   - `interpreter`

### Risk

The websocket layer currently expects an “agent-like” interface that was being satisfied by `ChatOrchestrator`.
The direct ReAct agent already covers most of this surface, but transitional smoothing may be needed.

### Recommended approach

If one method signature is slightly awkward, use a **tiny helper/adapter in runtime**, not a new orchestration wrapper. Tiny adapters are vitamins; new wrappers are houseplants.

## Phase 3 — Delete the root-RLM orchestration path

Once websocket runtime no longer depends on `ChatOrchestrator`, remove the dead top-level branch.

### Main files

- chat_orchestrator.py
- signatures.py
- rlm_runtime_modules.py
- config.py

### Work

1. Delete chat_orchestrator.py.
2. Remove root-routing/root-chat signatures:
   - `IntentRouterSignature`
   - `RoutedChatTurnSignature`
3. Remove `build_root_chat_rlm()`.
4. Remove `chat_execution_mode` from runtime config and wiring.

### Why this is safe

The only thing being removed here is the **root RLM path**, not the child RLM path.
That means recursion still exists where it’s valuable, but the duplicated top-level runtime disappears.

## Phase 4 — Preserve and clarify child-RLM delegation

This is where the current draft needs the biggest correction.

### Main files

- delegate_sub_agent.py
- delegate.py

### Work

1. Keep delegate_sub_agent.py.
2. Keep `spawn_delegate_sub_agent_async(...)` as the canonical child-RLM executor.
3. Keep `rlm_query` as a thin tool-level wrapper.
4. Preserve current guardrails:
   - max depth
   - delegate call budget
   - LM fallback tracking
   - result truncation

### Why

This child-RLM layer is already the right abstraction for “deep work as a tool.”
Replacing it with a brand-new direct `dspy.RLM` instantiation inside the tool would just move complexity, not remove it.

## Phase 5 — Add nested streaming

This is the most important behavioral enhancement.

### Main files

- delegate_sub_agent.py
- delegate.py
- streaming.py
- streaming_context.py

### Work

1. Pass the outer `_live_event_callback` from the agent through `rlm_query`.
2. Consume that callback inside delegate_sub_agent.py.
3. Stream child-RLM progress using DSPy streaming and existing event semantics.
4. Reuse existing event vocabulary instead of inventing new event kinds.
5. Enrich nested events with depth/runtime metadata.

### Intended user-visible effect

When `rlm_query` is called, the UI should be able to show a nested sequence like:

```text
tool call: rlm_query(...)
status: child RLM started
trajectory_step: inspect relevant section
trajectory_step: write Python to extract data
tool_result: child execution completed
final: assistant answer
```

### Constraint

Do not break the current `StreamEvent` contract used by:

- websocket envelopes
- execution step persistence
- frontend event adapters

Event-schema churn would expand scope dramatically.

## Phase 6 — Tests and verification

The test plan should be targeted, not vague.

### Delete / replace

- delete test_chat_orchestrator.py

### Update

- test_rlm_state.py
  - add nested callback/event forwarding coverage
- test_tools_sandbox.py
  - keep existing `rlm_query` behavior assertions
  - update only if call shapes change slightly
- test_chat_stream.py
  - remove obsolete expectations around root routing
  - keep websocket event contract coverage

### Mostly stable

These should remain largely intact:

- test_react_streaming.py
- test_session_isolation.py
- command tests
- session persistence tests
- most tool-layer tests

## Expected code tree, excluding `_scaffold`

Here is the expected affected target tree.

```text
src/fleet_rlm/
├── react/
│   ├── agent.py
│   ├── delegate_sub_agent.py
│   ├── rlm_runtime_modules.py
│   ├── signatures.py
│   ├── streaming.py
│   ├── streaming_citations.py
│   ├── streaming_context.py
│   ├── tools/
│   │   └── delegate.py
│   └── chat_orchestrator.py          # deleted
│
├── runners.py
│
├── server/
│   ├── config.py
│   └── routers/ws/
│       ├── chat_runtime.py
│       ├── streaming.py
│       └── chat_connection.py
│
tests/
├── unit/
│   ├── test_chat_orchestrator.py     # deleted
│   ├── test_rlm_state.py
│   ├── test_tools_sandbox.py
│   ├── test_react_streaming.py
│   └── test_react_agent.py
│
└── ui/ws/
    └── test_chat_stream.py
```

## LOC impact estimate

Excluding `src/fleet_rlm/_scaffold/**`, the expected delta for the affected slice is roughly:

```text
Delete chat_orchestrator.py                ~ -594
Delete test_chat_orchestrator.py           ~ -150
Trim root-only signatures/factories        ~ -50 to -90
Trim runners/config/runtime routing        ~ -35 to -65
Add nested callback/event forwarding       ~ +60 to +130
Add/adjust focused tests                   ~ +20 to +60
-----------------------------------------------
Estimated net reduction                    ~ -600 to -750
Reasonable headline estimate               ~ -650 LOC
```

### Why this is lower than the earlier draft

Because the earlier draft assumed deleting:

- delegate_sub_agent.py
- all of rlm_runtime_modules.py

I do **not** recommend either of those deletions.

This is still a major simplification, just an honest one.

## Verification plan

Run this in two passes.

### Pass 1 — runtime simplification

Verify that the websocket runtime works with the direct ReAct agent and no top-level root-RLM routing.

### Pass 2 — nested child-stream forwarding

Verify that `rlm_query` emits nested progress into the live stream.

### Focused automated checks

```text
uv run pytest -q tests/unit/test_tools_sandbox.py tests/unit/test_rlm_state.py tests/unit/test_react_streaming.py tests/ui/ws/test_chat_stream.py tests/ui/ws/test_session_isolation.py
uv run ruff check src tests
uv run ty check src --exclude "src/fleet_rlm/_scaffold/**"
```

### Manual UI verification

Use a prompt that should trigger `rlm_query`, for example:

- “write a Python script to solve a maze”
- or a document-heavy deep analysis task

Expected behavior:

1. top-level runtime uses `RLMReActChatAgent`
2. ReAct decides to call `rlm_query`
3. nested progress becomes visible in websocket stream
4. final answer returns normally
5. no root-RLM top-level mode is involved

## Decision summary

## Keep

- `RLMReActChatAgent` as top-level chat runtime
- child `dspy.RLM` delegation
- delegate_sub_agent.py
- existing event vocabulary
- recursion/budget/fallback guardrails

## Change

- websocket runtime bootstrap
- `rlm_query` callback plumbing
- nested streaming behavior
- tests and docs

## Delete

- `ChatOrchestrator`
- root-RLM chat mode
- root-chat signatures/factories
- `chat_execution_mode`
- orchestrator-specific tests

## Explicit non-goals

These are intentionally out of scope for the first implementation pass:

- renaming delegate_sub_agent.py
- redesigning runtime-module registries
- changing delegate budget or fallback semantics
- converting the system to a Daytona-style recursive tree runtime
- changing frontend event schema



It gives you the simplification you want without destroying the useful part of the current recursion model. It also fits the actual shape of the product:

- interactive
- stateful
- streamed
- tool-using
- selectively recursive

In short:

```text
Delete the duplicated top-level orchestration.
Keep recursion as a bounded tool.
Make child progress visible.
```
