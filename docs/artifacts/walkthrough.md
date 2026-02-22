# Walkthrough: Surgical Integration (Phases 1–3)

## Summary

This walkthrough documents the full journey of surgically integrating new capabilities into the existing `qredence/fleet-rlm` codebase without a ground-up rewrite.

---

## Phase 1: Evolutive Memory Integration ✅

**Goal:** Add Neon pgvector long-term memory without touching the existing `server/` layer.

### Changes Made

- **[NEW]** [schema.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/memory/schema.py) — `TaxonomyNode` and `AgentMemory` SQLModel tables with `Vector(1536)` embeddings
- **[NEW]** [db.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/memory/db.py) — Async `asyncpg` engine, session factory, `init_db()` with `CREATE EXTENSION IF NOT EXISTS vector`

### Verification

- `test_db.py` successfully connected to Neon, inserted a `TaxonomyNode` with a 1536-dim embedding, and queried it back.

**Commit:** `feat(memory): integrate pgvector evolutive db via sqlmodel and asyncpg`

---

## Phase 2: Context Guards & Memory Tools ✅

**Goal:** Protect the DSPy context window and wire memory into `@dspy.tool` decorators.

### Changes Made

- **[MODIFIED]** [sandbox.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/stateful/sandbox.py) — Injected `MAX_CHARS=2000` truncation guard into `execute_with_rlm()` output
- **[NEW]** [memory_tools.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/core/memory_tools.py) — `@dspy.tool search_evolutive_memory(query)` using LiteLLM embeddings + pgvector `l2_distance`

### Verification

- Confirmed truncation applies to any stdout > 2000 chars before DSPy ingestion.

**Commit:** `feat(core): implement modal stdout truncation guard and pgvector dspy tools`

---

## Phase 3: Agent Core Analysis ✅ (Pre-Built)

**Goal:** Separate ReAct Supervisor from RLM Engine.

### Critical Discovery

Deep analysis of the existing `react/` module revealed that **the dual-loop architecture is already fully implemented:**

| Component         | File                                                                                                                                                                           | What It Does                                                                     |
| :---------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------- |
| ReAct Supervisor  | [agent.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/react/agent.py)                             | `RLMReActChatAgent` wraps `dspy.ReAct` with chat history, core memory, streaming |
| RLM Modules       | [rlm_runtime_modules.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/react/rlm_runtime_modules.py) | 12+ `dspy.RLM` wrapper modules for long-context tasks                            |
| Module Factory    | [runtime_factory.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/react/runtime_factory.py)         | Lazy-loading, cached `dspy.RLM` construction                                     |
| Sub-Agent Spawner | [delegate_sub_agent.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/react/delegate_sub_agent.py)   | `spawn_delegate_sub_agent(depth+1)` with shared interpreter                      |
| Delegation Tools  | [tools_rlm_delegate.py](file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy/src/fleet_rlm/react/tools_rlm_delegate.py)   | 9+ tools: `rlm_query`, `analyze_long_document`, `plan_code_change`, etc.         |

### Remaining Micro-Gaps

1. Wire `search_evolutive_memory` into `build_tool_list()` (~5 LOC)
2. Verify truncation guard covers the `dspy.RLM` → `ModalInterpreter.execute()` path

---

## What's Next

**Phase 4: WebSocket Multiplexing** — The actual missing functionality. Expand `StreamEvent` payloads to include `rlm_executing`, `plan_update`, `memory_update` types for the frontend.

**Phase 5: React Frontend** — Build the Vite/Zustand dual-pane dashboard consuming Phase 4's WebSocket stream.
