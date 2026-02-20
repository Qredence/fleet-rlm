# fleet-rlm Agentic Architecture Plan

This document outlines the architectural impact on the current codebase across the 5 phases of the integration.

## User Review Required

> [!IMPORTANT]
> The architectural additions introduce new directories such as `api/`, `memory/`, and `agents/` inside `src/fleet_rlm`, complementing existing components. Please review carefully to ensure the paths align with your vision.

## Proposed Changes

---

### 1. Database & Infrastructure (`Phase 1`)

We will create the foundation for long-term Evolutive Taxonomy Memory and a standard FastAPI entrypoint.

#### [NEW] `schema.py`(file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/\_WORLD/\_RLM/fleet-rlm-dspy/src/fleet_rlm/memory/schema.py)

Creates SQLModel definitions for `TaxonomyNode` and `AgentMemory` with `pgvector` embeddings, and establishes the asyncpg connection manager over Neon.

#### [NEW] `main.py`(file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/\_WORLD/\_RLM/fleet-rlm-dspy/src/fleet_rlm/api/main.py)

Initializes a barebones FastAPI app and checks for `.env` credentials (`DATABASE_URL`, `MODAL_TOKEN_ID`, etc.).

---

### 2. Execution & Tools (`Phase 2`)

Building the core execution environment using Modal and wrapping it as DSPy tools.

#### [NEW] `modal_repl.py`(file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/\_WORLD/\_RLM/fleet-rlm-dspy/src/fleet_rlm/core/modal_repl.py)

Modal deployment stub and a persistent volume mounted at `/data/workspace`, providing stateful `execute_chunk` execution.

#### [NEW] `tools.py`(file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/\_WORLD/\_RLM/fleet-rlm-dspy/src/fleet_rlm/core/tools.py)

Exposes `@dspy.tool` logic for `execute_workspace_code` (enforcing max 2000 chars guard) and `search_evolutive_memory` (pgvector search).

---

### 3. Agentic Brains (`Phase 3`)

Defines the core intelligence pipeline utilizing DSPy `dspy.Signature` and `dspy.Module`.

#### [NEW] `signatures.py`(file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/\_WORLD/\_RLM/fleet-rlm-dspy/src/fleet_rlm/agents/signatures.py)

Contains `TaskDecomposer` and `CodeWriterSignature` defining strict IO primitives.

#### [NEW] `worker_rlm.py`(file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/\_WORLD/\_RLM/fleet-rlm-dspy/src/fleet_rlm/agents/worker_rlm.py)

Defines `RLMEngine` module which loops over decomposition and coding steps, invoking Modal execution and checking completion.

#### [NEW] `supervisor.py`(file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/\_WORLD/\_RLM/fleet-rlm-dspy/src/fleet_rlm/agents/supervisor.py)

A standard `dspy.ReAct` wrapper routing tasks to the `RLMEngine`.

#### [MODIFY] `engine.py`(file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/\_WORLD/\_RLM/fleet-rlm-dspy/src/fleet_rlm/core/engine.py)

Wraps LM setup with `posthog` telemetry tracing.

---

### 4. Telemetry Multiplexer & TUI (`Phase 4`)

Streams internal agent events gracefully without breaking existing TUI.

#### [NEW] `ws.py`(file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/\_WORLD/\_RLM/fleet-rlm-dspy/src/fleet_rlm/api/ws.py)

Provides `ws://localhost:8000/ws/stream` to multiplex `chat`, `plan_update`, `rlm_executing`, and `memory_update` events.

#### [MODIFY] `tui-cli/`(file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/\_WORLD/\_RLM/fleet-rlm-dspy/tui-cli) (various)

Intercepts payload types, keeping `chat` in the main log but isolating agent activity into side-panels using Rich/Textual.

---

### 5. Dual-Pane React Frontend (`Phase 5`)

Real-time UI updates representing agent architecture in a rich dashboard.

#### [MODIFY] `agentStore.ts`(file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/\_WORLD/\_RLM/fleet-rlm-dspy/src/frontend/src/store/agentStore.ts)

Adds specific zustand state slices parsing the websocket models.

#### [MODIFY] `useMemory.ts`(file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/\_WORLD/\_RLM/fleet-rlm-dspy/src/frontend/src/hooks/useMemory.ts)

Uses TanStack Query to keep UI synced with the Neon DB memory structures.

#### [MODIFY] `src/frontend/src/components/`(file:///Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/\_WORLD/\_RLM/fleet-rlm-dspy/src/frontend/src/components/)

Integrates a collapsible view showcasing real-time code execution and execution chunk summaries.

## Verification Plan

### Automated Tests

- Verification scripts generated per phase (`test_db.py`, walkthroughs for tools).

### Manual Verification

- Testing Modal connectivity locally.
- Ensuring TUI rendering remains robust during complex code blocks.
- Exploring React frontend interactions on `localhost`.
