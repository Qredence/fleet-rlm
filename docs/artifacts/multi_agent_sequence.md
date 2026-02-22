# Phase 6: Multi-Agent Sequence Diagram

This artifact provides a deep-dive sequence diagram for the **Multi-Agent Orchestration** flow introduced in Phase 6. It illustrates how the DSPy Supervisor routes intents, manages the lifecycle of Sub-Agents, interacts with the Neon Evolutive Memory, and streams updates back to the original React WebSocket client.

## 🌊 Execution Sequence

```mermaid
sequenceDiagram
    autonumber

    actor User as React User (Frontend)
    participant WS as FastAPI (ws.py)
    participant Sup as DSPy Supervisor Agent
    participant DB as Neon (pgvector Memory)
    participant Sub as DSPy Sub-Agent (Code/Research)
    participant Modal as Modal Sandbox (REPL)

    %% 1. Initialization & Prompt
    User->>WS: Sends strict JSON {"prompt": "Analyze dataset X, fix bug Y"}
    WS->>Sup: Initiates `RLMEngine.forward(prompt)`
    WS-->>User: Streams {"kind": "rlm_executing", "status": "Supervisor active"}

    %% 2. Context Gathering
    Sup->>DB: `search_evolutive_memory("Analysis of dataset X rules")`
    DB-->>Sup: Returns Top K `AgentMemory` contexts

    %% 3. Task Decomposition & Delegation
    Sup->>Sup: DSPy `TaskDecomposer` Signature (Break into 2 tasks)

    %% --- Sub-Task 1: The Researcher ---
    Sup->>Sub: Spawns Sub-Agent [Role: Researcher]
    WS-->>User: Streams {"kind": "plan_update", "status": "Sub-Agent 1 (Research) Spawned"}
    Sub->>DB: Queries memory for domain specific constraints
    DB-->>Sub: Returns specific constraints
    Sub-->>Sup: Returns Markdown Analysis Report
    Sup->>Sup: Ingests Sub-Task 1 Report

    %% --- Sub-Task 2: The Coder ---
    Sup->>Sub: Spawns Sub-Agent [Role: Coder]
    WS-->>User: Streams {"kind": "plan_update", "status": "Sub-Agent 2 (Coder) Spawned"}

    loop RLM Execution Loop (Sub-Agent 2)
        Sub->>Sub: Generates Python Sandbox Code
        Sub->>Modal: `execute_chunk(code)`
        Modal-->>Sub: Returns stdout/stderr (max 2000 chars guard)
        opt If Error
            Sub->>Sub: Self-Reflection & Retries
        end
    end

    Sub-->>Sup: Returns final calculated results or mutated datasets

    %% 4. Final Aggregation & Reflection
    Sup->>Sup: Aggregates Sub-Agent results into Final Response
    Sup->>DB: Writes new summary rule to `AgentMemory`
    WS-->>User: Streams {"kind": "memory_update", "status": "Memory crystallized"}

    %% 5. Completion
    Sup-->>WS: Yields final generated answer
    WS-->>User: Streams {"kind": "chat", "text": "Task complete. Fix Y applied to Dataset X."}
```

## Protocol Specifications

### Multiplexed WebSocket Payloads

As seen in the sequence above, the backend utilizes multiplexed JSON via SSE/WebSockets to communicate state transitions to the Zustand stores.

**1. `rlm_executing`**
Fired when a major agentic loop begins or shifts focus.

```json
{
  "kind": "rlm_executing",
  "text": "Supervisor actively delegating to Analyst Sub-Agent...",
  "depth": 0
}
```

**2. `plan_update`**
Fired specifically when the DSPy `TaskDecomposer` mutates its internal state list of pending/completed tasks.

```json
{
  "kind": "plan_update",
  "tasks": [
    "Research schema (Done)",
    "Write API wrapper (In Progress)",
    "Run tests (Pending)"
  ]
}
```

**3. `memory_update`**
Fired right before closing the loop, triggering TanStack `invalidateQueries` on the frontend.

```json
{
  "kind": "memory_update",
  "memory_id": "uuid-1234",
  "action": "crystallized"
}
```
