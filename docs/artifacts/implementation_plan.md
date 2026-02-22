# Fleet-RLM Next Evolution Implementation Plan

This document outlines the architectural and codebase impact for the next phases (6, 7, and 8) of the Fleet-RLM project.

## User Review Required

> [!IMPORTANT]
> The next evolution introduces advanced Multi-Agent delegation and background Memory Consolidation tasks. Please review the proposed architecture to ensure it aligns with the expected agent behavior and cloud cost budget.

## Proposed Changes

---

### A. Legacy Cleanup & Current State Verification (`Phase A`)

Prioritization pivot: Before expanding the Multi-Agent architecture, we must remove legacy debt and verify current sandbox boundaries.

#### [DELETE] `src/fleet_rlm/bridge/`(file:///Volumes/StorageBackup/\_RLM/fleet-rlm-dspy/src/fleet_rlm/bridge/)

Remove the deprecated TUI API bridge logic. The new FastAPI websocket routing supersedes this.

#### [MODIFY] `src/frontend/src/`(file:///Volumes/StorageBackup/\_RLM/fleet-rlm-dspy/src/frontend/src/)

Execute browser verification to confirm the UI correctly reflects Modal Volume files and parses external external documentation links via the active RLM Trajectories.

---

### 6. Multi-Agent Orchestration & Tool Expansion (`Phase 6`)

Adding depth to the ReAct capability by enabling multi-agent workflows and expanding the tool belt.

#### [MODIFY] `supervisor.py`(file:///Volumes/StorageBackup/\_RLM/fleet-rlm-dspy/src/fleet_rlm/agents/supervisor.py)

Update to support spawning specialized sub-agents with narrow context scopes, reducing token costs and hallucination.

#### [MODIFY] `tools.py`(file:///Volumes/StorageBackup/\_RLM/fleet-rlm-dspy/src/fleet_rlm/core/tools.py)

Implement new specialized tools targeting web intelligence and deep dataset analysis, complete with truncation guards.

---

### 7. Frontend Workspace Polish & Visualization (`Phase 7`)

Making the Evolutive Memory and Agent execution visually tangible to the user.

#### [MODIFY] `src/frontend/src/features/artifacts/`(file:///Volumes/StorageBackup/\_RLM/fleet-rlm-dspy/src/frontend/src/features/artifacts/)

Add new components for rendering dynamic interactive diagrams (e.g., `MemoryGraphView.tsx`) utilizing the real-time TanStack query cache.

#### [MODIFY] `src/frontend/src/components/ui/`(file:///Volumes/StorageBackup/\_RLM/fleet-rlm-dspy/src/frontend/src/components/ui/)

Build out a rich Markdown and syntax-highlighted block renderer for executed Python snippets returned from Modal.

---

### 8. Evolutive Memory Hardening (`Phase 8`)

Optimizing the semantic vector store for long-term agent persistency.

#### [MODIFY] `db.py`(file:///Volumes/StorageBackup/\_RLM/fleet-rlm-dspy/src/fleet_rlm/memory/db.py)

Introduce hybrid search combining traditional boolean text search with pgvector `l2_distance`.

#### [NEW] `reflection_worker.py`(file:///Volumes/StorageBackup/\_RLM/fleet-rlm-dspy/src/fleet_rlm/memory/reflection_worker.py)

An asynchronous job that periodically runs to merge, prune, and consolidate raw `AgentMemory` observations into dense `TaxonomyNode` rules.

## Verification Plan

### Automated Tests

- Run `pytest` for the new `reflection_worker.py` logic to ensure no data loss during consolidation.
- Run `vitest` for the new `MemoryGraphView` component ensuring correct prop mapping from Zustand.

### Manual Verification

- Execute a complex planning query in the TUI to visually confirm Multi-Agent delegation.
- Monitor Neon DB metrics during the background Reflection loop to ensure connection pools are not exhausted.
